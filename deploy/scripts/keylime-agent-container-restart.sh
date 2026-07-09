#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

export KEYLIME_AGENT_IMAGE="${KEYLIME_AGENT_IMAGE:-quay.io/keylime/keylime_agent:latest}"
export KEYLIME_AGENT_ENTRYPOINT="${KEYLIME_AGENT_ENTRYPOINT:-/usr/bin/keylime_agent}"
export KEYLIME_AGENT_UUID_FIXED="${KEYLIME_AGENT_UUID_FIXED:-11111111-1111-4111-8111-000000000009}"
export KEYLIME_AGENT_BIND_IP="${KEYLIME_AGENT_BIND_IP:-0.0.0.0}"
export KEYLIME_AGENT_IP="${KEYLIME_AGENT_IP:-172.31.100.9}"
export KEYLIME_AGENT_PORT="${KEYLIME_AGENT_PORT:-9002}"
export KEYLIME_REGISTRAR_IP="${KEYLIME_REGISTRAR_IP:-172.31.100.10}"
export KEYLIME_AGENT_REGISTRAR_PORT="${KEYLIME_AGENT_REGISTRAR_PORT:-8890}"
export KEYLIME_AGENT_BASE_DIR="${KEYLIME_AGENT_BASE_DIR:-/opt/keylime-agent-docker}"
export KEYLIME_AGENT_RELAX_TPM_PERMISSIONS="${KEYLIME_AGENT_RELAX_TPM_PERMISSIONS:-true}"
export KEYLIME_AGENT_CONTAINER_NAME="${KEYLIME_AGENT_CONTAINER_NAME:-keylime-agent}"

if [ -z "$KEYLIME_AGENT_UUID_FIXED" ] || [ -z "$KEYLIME_AGENT_IP" ]; then
  echo "ERROR: KEYLIME_AGENT_UUID_FIXED and KEYLIME_AGENT_IP are required."
  exit 1
fi

docker rm -f "$KEYLIME_AGENT_CONTAINER_NAME" 2>/dev/null || true

TPM_DEVICE=/dev/tpmrm0
[ -e "$TPM_DEVICE" ] || TPM_DEVICE=/dev/tpm0
if [ ! -e "$TPM_DEVICE" ]; then
  echo "ERROR: no TPM device found: /dev/tpmrm0 or /dev/tpm0"
  exit 1
fi

if [ "$KEYLIME_AGENT_RELAX_TPM_PERMISSIONS" = "true" ]; then
  [ -e /dev/tpmrm0 ] && chmod a+rw /dev/tpmrm0
  [ -e /dev/tpm0 ] && chmod a+rw /dev/tpm0
fi

install -d -m 0755 "$KEYLIME_AGENT_BASE_DIR/config"
install -d -m 0777 "$KEYLIME_AGENT_BASE_DIR/varlib"
install -d -m 0777 "$KEYLIME_AGENT_BASE_DIR/varlib/actions"
install -d -m 0777 "$KEYLIME_AGENT_BASE_DIR/logs"
chmod a+rwX "$KEYLIME_AGENT_BASE_DIR/varlib" "$KEYLIME_AGENT_BASE_DIR/varlib/actions" "$KEYLIME_AGENT_BASE_DIR/logs"

if [ ! -r "$KEYLIME_AGENT_BASE_DIR/config/cacert.crt" ]; then
  echo "ERROR: missing tenant CA: $KEYLIME_AGENT_BASE_DIR/config/cacert.crt"
  exit 1
fi

docker run -d \
  --name "$KEYLIME_AGENT_CONTAINER_NAME" \
  --restart unless-stopped \
  --network host \
  --privileged \
  --entrypoint "$KEYLIME_AGENT_ENTRYPOINT" \
  --device "$TPM_DEVICE:$TPM_DEVICE" \
  -e RUST_LOG=debug \
  -e TCTI="device:$TPM_DEVICE" \
  -e KEYLIME_AGENT_UUID="$KEYLIME_AGENT_UUID_FIXED" \
  -e KEYLIME_AGENT_IP="$KEYLIME_AGENT_BIND_IP" \
  -e KEYLIME_AGENT_PORT="$KEYLIME_AGENT_PORT" \
  -e KEYLIME_AGENT_CONTACT_IP="$KEYLIME_AGENT_IP" \
  -e KEYLIME_AGENT_CONTACT_PORT="$KEYLIME_AGENT_PORT" \
  -e KEYLIME_AGENT_REGISTRAR_IP="$KEYLIME_REGISTRAR_IP" \
  -e KEYLIME_AGENT_REGISTRAR_PORT="$KEYLIME_AGENT_REGISTRAR_PORT" \
  -e KEYLIME_AGENT_REGISTRAR_TLS_ENABLED="false" \
  -e KEYLIME_AGENT_ENABLE_AGENT_MTLS="true" \
  -e KEYLIME_AGENT_TRUSTED_CLIENT_CA="/etc/keylime/cacert.crt" \
  -e KEYLIME_AGENT_REVOCATION_ACTIONS_DIR="/var/lib/keylime/actions" \
  -v "$KEYLIME_AGENT_BASE_DIR/config:/etc/keylime:ro" \
  -v "$KEYLIME_AGENT_BASE_DIR/varlib:/var/lib/keylime:rw" \
  -v "$KEYLIME_AGENT_BASE_DIR/logs:/var/log/keylime:rw" \
  -v /sys/class/tpm:/sys/class/tpm:ro \
  -v /sys/kernel/security:/sys/kernel/security:ro \
  "$KEYLIME_AGENT_IMAGE"

sleep 8

docker ps --filter "name=$KEYLIME_AGENT_CONTAINER_NAME" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

docker logs --tail 200 "$KEYLIME_AGENT_CONTAINER_NAME"
ss -lntp | grep "$KEYLIME_AGENT_PORT" || true
