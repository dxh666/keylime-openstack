#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

LOCK_FILE="${PLACEMENT_SYNC_LOCK_FILE:-/run/keylime-openstack-sync.lock}"
SYNC_DIR="${KEYLIME_OPENSTACK_SYNC_DIR:-/opt/keylime-openstack-sync}"
SYNC_SCRIPT="${PLACEMENT_SYNC:-$SYNC_DIR/keylime-placement-sync.sh}"

if [ ! -x "$SYNC_SCRIPT" ]; then
  echo "ERROR: sync script is not executable: $SYNC_SCRIPT"
  exit 1
fi

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "Another keylime-openstack-sync run is still active; skip this round."
  exit 0
fi

exec "$SYNC_SCRIPT"

