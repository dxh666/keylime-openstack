#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"
SYNC_DIR="${KEYLIME_OPENSTACK_SYNC_DIR:-/opt/keylime-openstack-sync}"
MONITOR_URL="${KEYLIME_MONITOR_URL:-http://172.31.100.10:8088/api/status?force=1}"
AUDIT_FILE="${KEYLIME_VM_RISK_AUDIT_FILE:-/var/log/keylime-openstack-vm-risk-marker.json}"

section() {
  printf '\n=== %s ===\n' "$1"
}

section "Runtime services"
for unit in keylime-openstack-monitor.service keylime-openstack-sync.timer keylime-openstack-sync.service; do
  printf '%s active=%s enabled=%s\n' \
    "$unit" \
    "$(systemctl is-active "$unit" 2>/dev/null || true)" \
    "$(systemctl is-enabled "$unit" 2>/dev/null || true)"
done

section "Installed scripts"
for script in \
  keylime-agent-inventory-refresh.sh \
  keylime-placement-sync.sh \
  keylime-nova-compute-quarantine.sh \
  keylime-sync-control-loop.sh \
  keylime-vm-risk-marker.sh \
  keylime-openstack-trusted-flavor-setup.sh \
  keylime-tpm-evidence-audit.sh \
  keylime-tpm-pcr-policy-render-from-baseline.sh \
  keylime-tpm-pcr-policy-apply.sh; do
  path="$SYNC_DIR/$script"
  if [ -x "$path" ]; then
    echo "OK: $path"
  else
    echo "MISSING_OR_NOT_EXECUTABLE: $path"
  fi
done

section "Monitor API"
status_json="$(mktemp)"
trap 'rm -f "$status_json"' EXIT
if curl -fsS "$MONITOR_URL" > "$status_json"; then
  python3 - "$status_json" <<'PY'
import json
import sys

d = json.load(open(sys.argv[1]))
print("summary:", d.get("summary"))
for n in d.get("nodes", []):
    print(
        n.get("host"),
        "ip=", n.get("ip"),
        "vm_count=", n.get("vm_count"),
        "trust=", n.get("conclusion", {}).get("text"),
        "decision=", n.get("decision", {}).get("result"),
        "trait=", n.get("placement", {}).get("trait_present"),
        "service=", n.get("service", {}).get("status"), "/", n.get("service", {}).get("state"),
    )
PY
else
  echo "ERROR: monitor API is not reachable: $MONITOR_URL"
fi

if [ -r "$OPENRC" ]; then
  source "$OPENRC"
else
  echo "WARN: OpenStack RC file not readable: $OPENRC"
  exit 0
fi

section "OpenStack compute services"
openstack compute service list | awk 'NR==1 || /nova-compute/' || true

section "Placement trusted traits"
IFS=',' read -r -a hosts <<< "${KEYLIME_AGENT_HOSTS:-${COMPUTE_HOST:-${RP_NAME:-}}}"
for host in "${hosts[@]}"; do
  host="${host//[[:space:]]/}"
  [ -z "$host" ] && continue
  echo "--- $host ---"
  rp_uuid="$(openstack resource provider list --name "$host" -f value -c uuid 2>/dev/null | awk 'NF {print; exit}')"
  if [ -z "$rp_uuid" ]; then
    echo "MISSING_RESOURCE_PROVIDER"
    continue
  fi
  openstack resource provider trait list "$rp_uuid" | grep "${TRUSTED_TRAIT:-CUSTOM_KEYLIME_ATTESTED}" || \
    echo "MISSING: ${TRUSTED_TRAIT:-CUSTOM_KEYLIME_ATTESTED}"
done

section "Trusted flavors"
for flavor in "${PUBLIC_TRUSTED_FLAVOR:-trusted.keylime.small}" "${PRIVATE_TRUSTED_FLAVOR:-trusted.keylime.private.small}"; do
  echo "--- $flavor ---"
  openstack flavor show "$flavor" -c name -c os-flavor-access:is_public -c access_project_ids -c properties -f yaml 2>/dev/null || \
    echo "MISSING_FLAVOR"
done

section "VM risk marker"
echo "KEYLIME_VM_RISK_MARKER_ENABLE=${KEYLIME_VM_RISK_MARKER_ENABLE:-false}"
if [ -r "$AUDIT_FILE" ]; then
  python3 - "$AUDIT_FILE" <<'PY'
import json
import sys

d = json.load(open(sys.argv[1]))
mark = [a for a in d.get("actions", []) if a.get("action") == "MARK"]
clear = [a for a in d.get("actions", []) if a.get("action") == "CLEAR"]
print("audit_file:", sys.argv[1])
print("checked_at_utc:", d.get("checked_at_utc"))
print("summary:", d.get("summary"))
print("mark_count:", len(mark))
print("clear_count:", len(clear))
PY
else
  echo "NO_AUDIT_FILE: $AUDIT_FILE"
fi

section "TPM PCR policy management"
echo "KEYLIME_PCR_POLICY_FILE=${KEYLIME_PCR_POLICY_FILE:-/var/lib/keylime-openstack-sync/tpm-pcr-policies.json}"
echo "KEYLIME_TPM_EVIDENCE_BASELINE_JSON=${KEYLIME_TPM_EVIDENCE_BASELINE_JSON:-/var/log/keylime-openstack-tpm-evidence-baseline.json}"
echo "KEYLIME_POLICY_BASE_DIR=${KEYLIME_POLICY_BASE_DIR:-/var/lib/keylime-openstack-sync/policies}"
for file in \
  "${KEYLIME_TPM_EVIDENCE_BASELINE_JSON:-/var/log/keylime-openstack-tpm-evidence-baseline.json}" \
  "${KEYLIME_PCR_POLICY_FILE:-/var/lib/keylime-openstack-sync/tpm-pcr-policies.json}" \
  "${KEYLIME_POLICY_RENDER_AUDIT_FILE:-/var/log/keylime-openstack-policy-render.json}" \
  "${KEYLIME_POLICY_APPLY_AUDIT_FILE:-/var/log/keylime-openstack-policy-apply.json}"; do
  if [ -r "$file" ]; then
    echo "OK: $file"
  else
    echo "MISSING_OR_NOT_READABLE: $file"
  fi
done
