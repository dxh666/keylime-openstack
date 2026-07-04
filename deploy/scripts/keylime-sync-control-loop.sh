#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

LOCK_FILE="${CONTROL_LOOP_LOCK_FILE:-/run/keylime-openstack-control-loop.lock}"
SYNC_DIR="${KEYLIME_OPENSTACK_SYNC_DIR:-/opt/keylime-openstack-sync}"
INVENTORY_REFRESH="${INVENTORY_REFRESH:-$SYNC_DIR/keylime-agent-inventory-refresh.sh}"
PLACEMENT_SYNC="${PLACEMENT_SYNC:-$SYNC_DIR/keylime-placement-sync.sh}"
QUARANTINE_SYNC="${QUARANTINE_SYNC:-$SYNC_DIR/keylime-nova-compute-quarantine.sh}"
LOG_DIR="${KEYLIME_OPENSTACK_LOG_DIR:-/var/log}"

for script in "$PLACEMENT_SYNC" "$QUARANTINE_SYNC"; do
  if [ ! -x "$script" ]; then
    echo "ERROR: script is not executable: $script"
    exit 1
  fi
done

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "Another keylime-openstack control loop is still active; skip this round."
  exit 0
fi

is_truthy() {
  case "${1,,}" in
    1|true|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

if is_truthy "${KEYLIME_AGENT_INVENTORY_REFRESH:-false}"; then
  if [ -x "$INVENTORY_REFRESH" ]; then
    "$INVENTORY_REFRESH" || echo "WARN: Keylime agent inventory refresh failed; continue with current inventory."
    [ -f "$ENV_FILE" ] && source "$ENV_FILE"
  else
    echo "WARN: inventory refresh script is not executable: $INVENTORY_REFRESH"
  fi
fi

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

append_host() {
  local host
  host="$(trim "$1")"
  [ -z "$host" ] && return 0

  local existing
  for existing in "${HOSTS[@]}"; do
    [ "$existing" = "$host" ] && return 0
  done
  HOSTS+=("$host")
}

map_get() {
  local mapping="$1"
  local key="$2"
  local item item_key item_value

  IFS=',' read -r -a MAP_ITEMS <<< "$mapping"
  for item in "${MAP_ITEMS[@]}"; do
    item="$(trim "$item")"
    [ -z "$item" ] && continue
    [ "$item" = "${item#*=}" ] && continue
    item_key="$(trim "${item%%=*}")"
    item_value="$(trim "${item#*=}")"
    if [ "$item_key" = "$key" ]; then
      printf '%s' "$item_value"
      return 0
    fi
  done
  return 0
}

safe_name() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '_'
}

run_for_host() {
  local host="$1"
  local uuid="$2"
  local safe_host raw_status_file decision_file last_log_file placement_rc quarantine_rc

  if [ -z "$uuid" ]; then
    echo "WARN: skip $host because no Keylime agent UUID is configured."
    return 0
  fi

  safe_host="$(safe_name "$host")"
  if [ "$host" = "${COMPUTE_HOST:-${RP_NAME:-}}" ]; then
    raw_status_file="$LOG_DIR/keylime-openstack-sync-status.raw.log"
    decision_file="$LOG_DIR/keylime-openstack-sync-decision.json"
    last_log_file="$LOG_DIR/keylime-openstack-sync-last.log"
  else
    raw_status_file="$LOG_DIR/keylime-openstack-sync-status-${safe_host}.raw.log"
    decision_file="$LOG_DIR/keylime-openstack-sync-decision-${safe_host}.json"
    last_log_file="$LOG_DIR/keylime-openstack-sync-last-${safe_host}.log"
  fi

  echo "Sync Keylime/OpenStack trust state for $host"

  set +e
  KEYLIME_OPENSTACK_ENV_FILE="$ENV_FILE" \
  AGENT_UUID="$uuid" \
  RP_NAME="$host" \
  RAW_STATUS_FILE="$raw_status_file" \
  DECISION_FILE="$decision_file" \
  LAST_LOG_FILE="$last_log_file" \
    "$PLACEMENT_SYNC"
  placement_rc=$?
  set -e

  if [ "$placement_rc" -ne 0 ]; then
    echo "ERROR: placement sync failed for $host rc=$placement_rc"
    return "$placement_rc"
  fi

  set +e
  KEYLIME_OPENSTACK_ENV_FILE="$ENV_FILE" \
  COMPUTE_HOST="$host" \
  COMPUTE_SERVICE="${COMPUTE_SERVICE:-nova-compute}" \
  DECISION_FILE="$decision_file" \
    "$QUARANTINE_SYNC"
  quarantine_rc=$?
  set -e

  if [ "$quarantine_rc" -ne 0 ]; then
    echo "ERROR: nova-compute quarantine sync failed for $host rc=$quarantine_rc"
    return "$quarantine_rc"
  fi
}

declare -a HOSTS=()
if [ -n "${KEYLIME_AGENT_HOSTS:-}" ]; then
  IFS=',' read -r -a HOST_ITEMS <<< "$KEYLIME_AGENT_HOSTS"
  for item in "${HOST_ITEMS[@]}"; do
    append_host "$item"
  done
fi

append_host "${COMPUTE_HOST:-${RP_NAME:-}}"

if [ "${#HOSTS[@]}" -eq 0 ]; then
  echo "ERROR: no compute host or Keylime agent host configured."
  exit 1
fi

for host in "${HOSTS[@]}"; do
  uuid="$(map_get "${KEYLIME_AGENT_UUID_MAP:-}" "$host")"
  if [ -z "$uuid" ] && [ "$host" = "${COMPUTE_HOST:-${RP_NAME:-}}" ]; then
    uuid="${KEYLIME_AGENT_UUID_FIXED:-}"
  fi
  run_for_host "$host" "$uuid"
done
