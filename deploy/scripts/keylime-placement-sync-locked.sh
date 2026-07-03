#!/usr/bin/env bash
set -euo pipefail

LOCK_FILE=/run/keylime-openstack-sync.lock
SYNC_SCRIPT=/opt/keylime-openstack-sync/keylime-placement-sync.sh

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "Another keylime-openstack-sync run is still active; skip this round."
  exit 0
fi

exec "$SYNC_SCRIPT"


