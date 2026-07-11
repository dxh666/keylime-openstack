#!/usr/bin/env bash
set -euo pipefail

# Case 11: IMA runtime integrity / PCR10 control-plane commands.
# Run on csri10.

source /etc/keylime-openstack-sync/openstack-keylime-lab.env
source /etc/kolla/admin-openrc.sh
export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"

show_status() {
  curl -s "http://172.31.100.10:8088/api/status?force=1" > /tmp/keylime-status-case11.json
  python3 - <<'PY'
import json
d = json.load(open("/tmp/keylime-status-case11.json"))
print("summary:", d.get("summary"))
for n in d.get("nodes", []):
    layers = n.get("trust_layers", {})
    runtime = layers.get("runtime", {})
    policy = layers.get("policy", {})
    print(
        n.get("host"),
        "trust=", n.get("conclusion", {}).get("text"),
        "decision=", n.get("decision", {}).get("result"),
        "runtime=", runtime.get("text"),
        "runtime_code=", runtime.get("code"),
        "has_runtime_policy=", policy.get("has_runtime_policy"),
        "pcr10=", policy.get("runtime_pcr10_enforced"),
        "trait=", n.get("placement", {}).get("trait_present"),
        "service=", n.get("service", {}).get("status"), "/", n.get("service", {}).get("state"),
        "vm_count=", n.get("vm_count"),
    )
PY
}

case "${1:-}" in
  audit)
    /opt/keylime-openstack-sync/keylime-ima-runtime-evidence-audit.sh
    ;;
  register)
    target="${2:-}"
    runtime_policy_json="${3:-}"
    policy_id="${4:-}"
    display_name="${5:-}"
    if [ -z "$target" ] || [ -z "$runtime_policy_json" ]; then
      echo "Usage: $0 register <host|all|host1,host2> <runtime_policy_json> [policy_id] [display_name]" >&2
      exit 1
    fi
    /opt/keylime-openstack-sync/keylime-ima-runtime-policy-register.sh \
      "$target" "$runtime_policy_json" "$policy_id" "$display_name"
    ;;
  apply-bound)
    /opt/keylime-openstack-sync/keylime-ima-runtime-policy-apply.sh all bound
    sleep 40
    systemctl start keylime-openstack-sync.service || true
    sleep 10
    KEYLIME_VM_RISK_MARKER_FORCE=true /opt/keylime-openstack-sync/keylime-vm-risk-marker.sh || true
    show_status
    ;;
  generate)
    target="${2:-}"
    policy_id="${3:-}"
    display_name="${4:-}"
    if [ -z "$target" ]; then
      echo "Usage: $0 generate <host> [policy_id] [display_name]" >&2
      exit 1
    fi
    /opt/keylime-openstack-sync/keylime-ima-runtime-policy-generate.sh \
      "$target" "$policy_id" "$display_name"
    ;;
  apply)
    target="${2:-}"
    policy_id="${3:-bound}"
    if [ -z "$target" ]; then
      echo "Usage: $0 apply <host|all> [policy_id|bound]" >&2
      exit 1
    fi
    /opt/keylime-openstack-sync/keylime-ima-runtime-policy-apply.sh "$target" "$policy_id"
    sleep 40
    systemctl start keylime-openstack-sync.service || true
    sleep 10
    KEYLIME_VM_RISK_MARKER_FORCE=true /opt/keylime-openstack-sync/keylime-vm-risk-marker.sh || true
    show_status
    ;;
  status)
    show_status
    ;;
  audit-files)
    python3 -m json.tool "${KEYLIME_IMA_RUNTIME_BASELINE_JSON:-/var/log/keylime-openstack-ima-runtime-baseline.json}" || true
    python3 -m json.tool "${KEYLIME_RUNTIME_POLICY_REGISTER_AUDIT_FILE:-/var/log/keylime-openstack-runtime-policy-register.json}" || true
    python3 -m json.tool "${KEYLIME_RUNTIME_POLICY_APPLY_AUDIT_FILE:-/var/log/keylime-openstack-runtime-policy-apply.json}" || true
    ;;
  *)
    cat <<'EOF'
Usage:
  case11-ima-runtime-integrity-commands.sh audit
  case11-ima-runtime-integrity-commands.sh generate <host> [policy_id] [display_name]
  case11-ima-runtime-integrity-commands.sh register <host|all|host1,host2> <runtime_policy_json> [policy_id] [display_name]
  case11-ima-runtime-integrity-commands.sh apply-bound
  case11-ima-runtime-integrity-commands.sh apply <host|all> [policy_id|bound]
  case11-ima-runtime-integrity-commands.sh status
  case11-ima-runtime-integrity-commands.sh audit-files

Generate the runtime_policy_json with the Keylime runtime policy tooling that matches
the deployed Keylime version, then register and apply it with this command wrapper.
EOF
    ;;
esac
