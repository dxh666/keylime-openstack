#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

LOCK_FILE="${CONTROL_LOOP_LOCK_FILE:-/run/keylime-openstack-control-loop.lock}"
SYNC_DIR="${KEYLIME_OPENSTACK_SYNC_DIR:-/opt/keylime-openstack-sync}"
PLACEMENT_SYNC="${PLACEMENT_SYNC:-$SYNC_DIR/keylime-placement-sync.sh}"
QUARANTINE_SYNC="${QUARANTINE_SYNC:-$SYNC_DIR/keylime-nova-compute-quarantine.sh}"

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

"$PLACEMENT_SYNC"
"$QUARANTINE_SYNC"

