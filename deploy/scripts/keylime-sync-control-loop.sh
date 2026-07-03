#!/usr/bin/env bash
set -euo pipefail

LOCK_FILE=/run/keylime-openstack-control-loop.lock
PLACEMENT_SYNC=/opt/keylime-openstack-sync/keylime-placement-sync.sh
QUARANTINE_SYNC=/opt/keylime-openstack-sync/keylime-nova-compute-quarantine.sh

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "Another keylime-openstack control loop is still active; skip this round."
  exit 0
fi

"$PLACEMENT_SYNC"
"$QUARANTINE_SYNC"


