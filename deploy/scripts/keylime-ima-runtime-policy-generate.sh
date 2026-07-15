#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-ima-runtime-policy-generate.sh <host> [policy_id] [display_name]

Examples:
  keylime-ima-runtime-policy-generate.sh hygon22
  keylime-ima-runtime-policy-generate.sh csri8 csri8-runtime-guard

Collects the live IMA ascii_runtime_measurements file from a compute host,
generates a Keylime runtime policy with the deployed keylime-policy tool, and
registers/binds the generated policy in the local policy store.

Default dynamic excludes cover Docker transient container config files. Extend
the exclude file before running if a host has additional expected transient
paths:

  /var/lib/keylime-openstack-sync/policies/runtime/<host>-runtime-exclude.txt
EOF
}

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -r "$ENV_FILE" ] && source "$ENV_FILE"

HOST="${1:-}"
POLICY_ID="${2:-}"
DISPLAY_NAME="${3:-}"

if [ "$HOST" = "-h" ] || [ "$HOST" = "--help" ] || [ -z "$HOST" ]; then
  usage
  if [ -z "$HOST" ]; then
    exit 1
  fi
  exit 0
fi

KEYLIME_DIR="${KEYLIME_DIR:-/opt/keylime-docker}"
STATE_DIR="${KEYLIME_OPENSTACK_STATE_DIR:-/var/lib/keylime-openstack-sync}"
POLICY_BASE="${KEYLIME_POLICY_BASE_DIR:-$STATE_DIR/policies}"
RUNTIME_DIR="$POLICY_BASE/runtime"
EVIDENCE_DIR="${KEYLIME_RUNTIME_POLICY_EVIDENCE_DIR:-$RUNTIME_DIR/evidence-clean}"
REGISTER_SCRIPT="${KEYLIME_RUNTIME_POLICY_REGISTER_SCRIPT:-${KEYLIME_OPENSTACK_SYNC_DIR:-/opt/keylime-openstack-sync}/keylime-ima-runtime-policy-register.sh}"
SSH_OPTS="${KEYLIME_RUNTIME_POLICY_SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no}"
AGENT_IP_OVERRIDE="${KEYLIME_RUNTIME_POLICY_HOST_IP:-}"
REGISTER_POLICY="${KEYLIME_RUNTIME_POLICY_GENERATE_REGISTER:-true}"
BASE_POLICY="${KEYLIME_RUNTIME_POLICY_BASE_POLICY:-}"
EXCLUDE_FILE="${KEYLIME_RUNTIME_POLICY_EXCLUDE_FILE:-$RUNTIME_DIR/${HOST}-runtime-exclude.txt}"
EXTRA_EXCLUDES="${KEYLIME_RUNTIME_POLICY_EXTRA_EXCLUDES:-}"

parse_map_value() {
  local map="$1"
  local key="$2"
  echo "$map" | tr ',' '\n' | awk -F= -v k="$key" '$1 == k {print $2; exit}'
}

HOST_IP="$AGENT_IP_OVERRIDE"
if [ -z "$HOST_IP" ]; then
  HOST_IP="$(parse_map_value "${KEYLIME_AGENT_IP_MAP:-}" "$HOST")"
fi
if [ -z "$HOST_IP" ]; then
  echo "ERROR: cannot resolve IP for host '$HOST'. Set KEYLIME_AGENT_IP_MAP or KEYLIME_RUNTIME_POLICY_HOST_IP." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$EVIDENCE_DIR"

if [ ! -e "$EXCLUDE_FILE" ]; then
  cat > "$EXCLUDE_FILE" <<'EOF'
^/var/lib/docker/containers/[0-9a-f]+/\.tmp-config\.v2\.json.*$
^/var/lib/docker/containers/[0-9a-f]+/\.tmp-hostconfig\.json.*$
EOF
fi
if [ -n "$EXTRA_EXCLUDES" ]; then
  printf '%s\n' "$EXTRA_EXCLUDES" >> "$EXCLUDE_FILE"
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
POLICY_ID="${POLICY_ID:-${HOST}-runtime-guard-$TS}"
DISPLAY_NAME="${DISPLAY_NAME:-$POLICY_ID}"
RUNTIME_FILE="$POLICY_ID.json"
RUNTIME_PATH="$RUNTIME_DIR/$RUNTIME_FILE"
LIVE_FILE="$EVIDENCE_DIR/${HOST}-ima-live-$TS.txt"

echo "host=$HOST"
echo "host_ip=$HOST_IP"
echo "live_ima_file=$LIVE_FILE"
echo "exclude_file=$EXCLUDE_FILE"
echo "runtime_policy=$RUNTIME_PATH"

# shellcheck disable=SC2086
ssh $SSH_OPTS "root@$HOST_IP" 'cat /sys/kernel/security/ima/ascii_runtime_measurements' > "$LIVE_FILE"

line_count="$(wc -l < "$LIVE_FILE" | tr -d ' ')"
if [ "$line_count" = "0" ]; then
  echo "ERROR: live IMA measurement list is empty: $LIVE_FILE" >&2
  exit 1
fi

invalid_digest_count="$(awk '$1 ~ /^[0-9]+$/ && $4 !~ /^(sha1|sha256|sha384|sha512|sm3_256):/ {count++} END {print count+0}' "$LIVE_FILE")"
if [ "$invalid_digest_count" != "0" ]; then
  echo "ERROR: live IMA measurement list contains unsupported digest algorithms." >&2
  awk '$1 ~ /^[0-9]+$/ && $4 !~ /^(sha1|sha256|sha384|sha512|sm3_256):/ {print NR ":" $0; n++; if (n>=20) exit}' "$LIVE_FILE" >&2
  exit 1
fi

policy_args=(
  create runtime
  -m "/keylime-ima/$(basename "$LIVE_FILE")"
  -e "/keylime-runtime-policy/$(basename "$EXCLUDE_FILE")"
  -o "/keylime-runtime-policy/$RUNTIME_FILE"
)
if [ -n "$BASE_POLICY" ]; then
  policy_args+=(-p "/keylime-runtime-policy/$(basename "$BASE_POLICY")")
fi

(
  cd "$KEYLIME_DIR"
  docker compose run --rm \
    -v "$EVIDENCE_DIR:/keylime-ima:ro" \
    -v "$RUNTIME_DIR:/keylime-runtime-policy:rw" \
    --entrypoint keylime-policy \
    keylime-tenant \
    "${policy_args[@]}"
)

test -s "$RUNTIME_PATH"

python3 - "$RUNTIME_PATH" "$LIVE_FILE" "$EXCLUDE_FILE" <<'PY'
import json
import sys
policy_path, live_file, exclude_file = sys.argv[1:4]
d = json.load(open(policy_path, encoding="utf-8"))
print("policy:", policy_path)
print("live_ima_file:", live_file)
print("exclude_file:", exclude_file)
print("meta:", d.get("meta"))
print("digests_count:", len(d.get("digests", {})))
print("excludes_count:", len(d.get("excludes", [])) if isinstance(d.get("excludes"), list) else 0)
PY

case "${REGISTER_POLICY,,}" in
  1|true|yes|y|on)
    KEYLIME_RUNTIME_POLICY_COPY=false \
    KEYLIME_RUNTIME_EXCLUDES="$(cat "$EXCLUDE_FILE")" \
      bash "$REGISTER_SCRIPT" "$HOST" "$RUNTIME_PATH" "$POLICY_ID" "$DISPLAY_NAME"
    ;;
  *)
    echo "register_skipped=true"
    ;;
esac
