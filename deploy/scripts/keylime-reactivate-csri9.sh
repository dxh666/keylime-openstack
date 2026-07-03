#!/usr/bin/env bash
set -euo pipefail

cd /opt/keylime-docker

KEYLIME_AGENT_UUID_FIXED="${KEYLIME_AGENT_UUID_FIXED:-11111111-1111-4111-8111-000000000009}"

docker compose run --rm keylime-tenant \
  -c reactivate \
  -u "$KEYLIME_AGENT_UUID_FIXED" \
  -v 172.31.100.10 \
  -vp 8881 \
  -r 172.31.100.10 \
  -rp 8891


