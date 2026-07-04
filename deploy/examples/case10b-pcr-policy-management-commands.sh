#!/usr/bin/env bash
set -euo pipefail

# Case 10B: TPM PCR policy management control plane.
# Run on csri10.

source /etc/keylime-openstack-sync/openstack-keylime-lab.env
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"

show_status() {
  curl -s "http://172.31.100.10:8088/api/status?force=1" > /tmp/keylime-status-case10b.json
  python3 - <<'PY'
import json
d = json.load(open("/tmp/keylime-status-case10b.json"))
print("summary:", d.get("summary"))
for n in d.get("nodes", []):
    print(
        n.get("host"),
        "trust=", n.get("conclusion", {}).get("text"),
        "decision=", n.get("decision", {}).get("result"),
        "reason=", n.get("decision", {}).get("reason"),
        "trait=", n.get("placement", {}).get("trait_present"),
        "service=", n.get("service", {}).get("status"), "/", n.get("service", {}).get("state"),
        "vm_count=", n.get("vm_count"),
    )
PY
}

case "${1:-}" in
  audit)
    /opt/keylime-openstack-sync/keylime-tpm-evidence-audit.sh
    ;;
  render)
    /opt/keylime-openstack-sync/keylime-tpm-pcr-policy-render-from-baseline.sh
    ;;
  apply-bound)
    /opt/keylime-openstack-sync/keylime-tpm-pcr-policy-apply.sh all bound
    sleep 10
    show_status
    ;;
  bad-csri8)
    /opt/keylime-openstack-sync/keylime-tpm-pcr-policy-apply.sh csri8 bad-sha256-pcr7-zero
    sleep 40
    systemctl start keylime-openstack-sync.service || true
    sleep 10
    KEYLIME_VM_RISK_MARKER_FORCE=true /opt/keylime-openstack-sync/keylime-vm-risk-marker.sh || true
    show_status
    ;;
  restore-csri8)
    /opt/keylime-openstack-sync/keylime-tpm-pcr-policy-apply.sh csri8 csri8-sha256-pcr7-baseline
    sleep 40
    systemctl start keylime-openstack-sync.service || true
    sleep 10
    KEYLIME_VM_RISK_MARKER_FORCE=true /opt/keylime-openstack-sync/keylime-vm-risk-marker.sh || true
    show_status
    ;;
  status)
    show_status
    ;;
  *)
    cat <<'EOF'
Usage:
  case10b-pcr-policy-management-commands.sh audit
  case10b-pcr-policy-management-commands.sh render
  case10b-pcr-policy-management-commands.sh apply-bound
  case10b-pcr-policy-management-commands.sh bad-csri8
  case10b-pcr-policy-management-commands.sh restore-csri8
  case10b-pcr-policy-management-commands.sh status
EOF
    ;;
esac

