#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"
source "$OPENRC"

DOMAIN="${DOMAIN:-Default}"
TRUSTED_PROJECT="${TRUSTED_PROJECT:-proj-boundary-a}"
SOURCE_FLAVOR="${SOURCE_FLAVOR:-m1.small}"
PUBLIC_TRUSTED_FLAVOR="${PUBLIC_TRUSTED_FLAVOR:-trusted.keylime.small}"
PRIVATE_TRUSTED_FLAVOR="${PRIVATE_TRUSTED_FLAVOR:-trusted.keylime.private.small}"
TRUSTED_TRAIT="${TRUSTED_TRAIT:-CUSTOM_KEYLIME_ATTESTED}"

FLAVOR_RAM="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c ram)"
FLAVOR_DISK="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c disk)"
FLAVOR_VCPUS="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c vcpus)"

ensure_flavor() {
  local flavor="$1"
  local visibility="$2"
  shift 2

  if openstack flavor show "$flavor" >/dev/null 2>&1; then
    echo "Flavor $flavor already exists"
  else
    openstack flavor create "$flavor" \
      "$visibility" \
      --id auto \
      --ram "$FLAVOR_RAM" \
      --disk "$FLAVOR_DISK" \
      --vcpus "$FLAVOR_VCPUS"
  fi

  openstack flavor set "$flavor" \
    --property "trait:${TRUSTED_TRAIT}=required"
}

ensure_flavor "$PUBLIC_TRUSTED_FLAVOR" "--public"
ensure_flavor "$PRIVATE_TRUSTED_FLAVOR" "--private"

set +e
project_access_out="$(
  openstack flavor set "$PRIVATE_TRUSTED_FLAVOR" \
    --project "$TRUSTED_PROJECT" \
    --project-domain "$DOMAIN" 2>&1
)"
project_access_rc=$?
set -e

if [ "$project_access_rc" -eq 0 ]; then
  echo "Granted $TRUSTED_PROJECT access to $PRIVATE_TRUSTED_FLAVOR"
elif [[ "$project_access_out" == *"Flavor access already exists"* || "$project_access_out" == *"ConflictException: 409"* ]]; then
  echo "Flavor access already exists for $TRUSTED_PROJECT on $PRIVATE_TRUSTED_FLAVOR"
else
  printf '%s\n' "$project_access_out"
  exit "$project_access_rc"
fi

for flavor in "$PUBLIC_TRUSTED_FLAVOR" "$PRIVATE_TRUSTED_FLAVOR"; do
  echo "--- $flavor ---"
  openstack flavor show "$flavor" \
    -c name \
    -c os-flavor-access:is_public \
    -c access_project_ids \
    -c properties \
    -f yaml
done
