#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

KEYLIME_DIR="${KEYLIME_DIR:-/opt/keylime-docker}"
KEYLIME_AGENT_UUID_FIXED="${KEYLIME_AGENT_UUID_FIXED:-11111111-1111-4111-8111-000000000009}"
KEYLIME_VERIFIER_IP="${KEYLIME_VERIFIER_IP:-172.31.100.10}"
KEYLIME_VERIFIER_PORT="${KEYLIME_VERIFIER_PORT:-8881}"
KEYLIME_REGISTRAR_IP="${KEYLIME_REGISTRAR_IP:-172.31.100.10}"
KEYLIME_REGISTRAR_PORT="${KEYLIME_REGISTRAR_PORT:-8891}"

if [ ! -d "$KEYLIME_DIR" ]; then
  echo "ERROR: Keylime docker directory not found: $KEYLIME_DIR"
  exit 1
fi

cd "$KEYLIME_DIR"

docker compose run --rm keylime-tenant \
  -c reactivate \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v "$KEYLIME_VERIFIER_IP" \
  -vp "$KEYLIME_VERIFIER_PORT" \
  -r "$KEYLIME_REGISTRAR_IP" \
  -rp "$KEYLIME_REGISTRAR_PORT"
