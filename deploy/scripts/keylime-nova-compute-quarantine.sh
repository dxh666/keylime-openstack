#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"
LOG_DIR="${KEYLIME_OPENSTACK_LOG_DIR:-/var/log}"
DECISION_FILE="${DECISION_FILE:-$LOG_DIR/keylime-openstack-sync-decision.json}"
STATE_DIR="${KEYLIME_OPENSTACK_STATE_DIR:-/var/lib/keylime-openstack-sync}"

COMPUTE_HOST="${COMPUTE_HOST:-csri9}"
COMPUTE_SERVICE="${COMPUTE_SERVICE:-nova-compute}"
MARKER_FILE="$STATE_DIR/${COMPUTE_HOST}.${COMPUTE_SERVICE}.disabled-by-keylime"

if [ ! -r "$OPENRC" ]; then
  echo "ERROR: OpenStack RC file not readable: $OPENRC"
  exit 1
fi

source "$OPENRC"

if [ ! -s "$DECISION_FILE" ]; then
  echo "ERROR: decision file not found or empty: $DECISION_FILE"
  exit 1
fi

read -r result reason attestation_status operational_state last_event_id < <(
  python3 - "$DECISION_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    d = json.load(f)

print(
    d.get("result", ""),
    d.get("reason", ""),
    d.get("attestation_status", ""),
    d.get("operational_state", ""),
    d.get("last_event_id", ""),
)
PY
)

disable_compute() {
  local disable_reason
  disable_reason="Keylime attestation not trusted: result=${result}, reason=${reason}, state=${operational_state}, event=${last_event_id}"

  echo "Keylime result is $result/$reason: disable ${COMPUTE_HOST} ${COMPUTE_SERVICE}"

  openstack compute service set \
    --disable \
    --disable-reason "$disable_reason" \
    "$COMPUTE_HOST" \
    "$COMPUTE_SERVICE"

  install -d -m 0755 "$STATE_DIR"
  {
    date -u +"%Y-%m-%dT%H:%M:%SZ"
    echo "$disable_reason"
  } > "$MARKER_FILE"
}

enable_compute_if_owned() {
  if [ -f "$MARKER_FILE" ]; then
    echo "Keylime result is PASS_FRESH and marker exists: enable ${COMPUTE_HOST} ${COMPUTE_SERVICE}"

    openstack compute service set \
      --enable \
      "$COMPUTE_HOST" \
      "$COMPUTE_SERVICE"

    rm -f "$MARKER_FILE"
  else
    echo "Keylime result is PASS_FRESH but no Keylime marker exists; do not override manual admin state."
  fi
}

if [ "$result" = "PASS_FRESH" ]; then
  enable_compute_if_owned
else
  disable_compute
fi

echo "Current compute service state:"
openstack compute service list | awk -v host="$COMPUTE_HOST" -v svc="$COMPUTE_SERVICE" 'NR==1 || ($0 ~ svc && $0 ~ host)'
