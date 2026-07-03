#!/usr/bin/env bash
set -euo pipefail

source /etc/kolla/admin-openrc.sh
source ./openstack-keylime-lab.env 2>/dev/null || true

DOMAIN="${DOMAIN:-Default}"
TRUSTED_PROJECT="${TRUSTED_PROJECT:-proj-boundary-a}"
ORDINARY_PROJECT="${ORDINARY_PROJECT:-proj-boundary-b}"
SOURCE_FLAVOR="${SOURCE_FLAVOR:-m1.small}"
PRIVATE_TRUSTED_FLAVOR="${PRIVATE_TRUSTED_FLAVOR:-trusted.keylime.private.small}"
TRUSTED_TRAIT="${TRUSTED_TRAIT:-CUSTOM_KEYLIME_ATTESTED}"

FLAVOR_RAM="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c ram)"
FLAVOR_DISK="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c disk)"
FLAVOR_VCPUS="$(openstack flavor show "$SOURCE_FLAVOR" -f value -c vcpus)"

if openstack flavor show "$PRIVATE_TRUSTED_FLAVOR" >/dev/null 2>&1; then
  echo "Flavor $PRIVATE_TRUSTED_FLAVOR already exists"
else
  openstack flavor create "$PRIVATE_TRUSTED_FLAVOR" \
    --private \
    --id auto \
    --ram "$FLAVOR_RAM" \
    --disk "$FLAVOR_DISK" \
    --vcpus "$FLAVOR_VCPUS"
fi

openstack flavor set "$PRIVATE_TRUSTED_FLAVOR" \
  --property "trait:${TRUSTED_TRAIT}=required"

openstack flavor set "$PRIVATE_TRUSTED_FLAVOR" \
  --project "$TRUSTED_PROJECT" \
  --project-domain "$DOMAIN"

openstack flavor show "$PRIVATE_TRUSTED_FLAVOR" \
  -c name \
  -c os-flavor-access:is_public \
  -c access_project_ids \
  -c properties \
  -f yaml


