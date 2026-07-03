#!/usr/bin/env bash
set -euo pipefail

export KEYLIME_AGENT_IMAGE="${KEYLIME_AGENT_IMAGE:-quay.io/keylime/keylime_agent:latest}"
export KEYLIME_AGENT_ENTRYPOINT="${KEYLIME_AGENT_ENTRYPOINT:-/usr/bin/keylime_agent}"
export KEYLIME_AGENT_UUID_FIXED="${KEYLIME_AGENT_UUID_FIXED:-11111111-1111-4111-8111-000000000009}"

docker rm -f keylime-agent 2>/dev/null || true

TPM_DEVICE=/dev/tpmrm0
[ -e "$TPM_DEVICE" ] || TPM_DEVICE=/dev/tpm0

[ -e /dev/tpmrm0 ] && chmod a+rw /dev/tpmrm0
[ -e /dev/tpm0 ] && chmod a+rw /dev/tpm0

install -d -m 0777 /opt/keylime-agent-docker/varlib/actions
install -d -m 0777 /opt/keylime-agent-docker/logs

docker run -d \
  --name keylime-agent \
  --restart unless-stopped \
  --network host \
  --privileged \
  --entrypoint "$KEYLIME_AGENT_ENTRYPOINT" \
  --device "$TPM_DEVICE:$TPM_DEVICE" \
  -e RUST_LOG=debug \
  -e TCTI="device:$TPM_DEVICE" \
  -e KEYLIME_AGENT_UUID="$KEYLIME_AGENT_UUID_FIXED" \
  -e KEYLIME_AGENT_IP="0.0.0.0" \
  -e KEYLIME_AGENT_PORT="9002" \
  -e KEYLIME_AGENT_CONTACT_IP="172.31.100.9" \
  -e KEYLIME_AGENT_CONTACT_PORT="9002" \
  -e KEYLIME_AGENT_REGISTRAR_IP="172.31.100.10" \
  -e KEYLIME_AGENT_REGISTRAR_PORT="8890" \
  -e KEYLIME_AGENT_REGISTRAR_TLS_ENABLED="false" \
  -e KEYLIME_AGENT_ENABLE_AGENT_MTLS="true" \
  -e KEYLIME_AGENT_TRUSTED_CLIENT_CA="/etc/keylime/cacert.crt" \
  -e KEYLIME_AGENT_REVOCATION_ACTIONS_DIR="/var/lib/keylime/actions" \
  -v /opt/keylime-agent-docker/config:/etc/keylime:ro \
  -v /opt/keylime-agent-docker/varlib:/var/lib/keylime:rw \
  -v /opt/keylime-agent-docker/logs:/var/log/keylime:rw \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE"

sleep 8

docker ps --filter name=keylime-agent \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

docker logs --tail 200 keylime-agent
ss -lntp | grep 9002 || true


