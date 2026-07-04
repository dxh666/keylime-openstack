#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"
LOG_DIR="${KEYLIME_OPENSTACK_LOG_DIR:-/var/log}"
MONITOR_URL="${KEYLIME_MONITOR_URL:-http://172.31.100.10:8088/api/status?force=1}"
AUDIT_FILE="${KEYLIME_VM_RISK_AUDIT_FILE:-$LOG_DIR/keylime-openstack-vm-risk-marker.json}"
REFRESH_SECONDS="${KEYLIME_VM_RISK_MARKER_INTERVAL_SECONDS:-60}"
FORCE="${KEYLIME_VM_RISK_MARKER_FORCE:-false}"

is_truthy() {
  case "${1,,}" in
    1|true|yes|y|on) return 0 ;;
    *) return 1 ;;
  esac
}

if [ ! -r "$OPENRC" ]; then
  echo "ERROR: OpenStack RC file not readable: $OPENRC"
  exit 1
fi

if [ -s "$AUDIT_FILE" ] && ! is_truthy "$FORCE"; then
  now="$(date +%s)"
  mtime="$(stat -c %Y "$AUDIT_FILE" 2>/dev/null || echo 0)"
  age=$((now - mtime))
  if [ "$age" -lt "$REFRESH_SECONDS" ]; then
    echo "VM risk marker audit is fresh enough: $AUDIT_FILE age=${age}s"
    exit 0
  fi
fi

source "$OPENRC"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

status_json="$tmp_dir/status.json"
servers_json="$tmp_dir/servers.json"
plan_tsv="$tmp_dir/plan.tsv"
checked_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

install -d -m 0755 "$(dirname "$AUDIT_FILE")"

curl -fsS "$MONITOR_URL" > "$status_json"
openstack server list --all-projects --long -f json -c ID -c Name -c Status -c Host > "$servers_json"

python3 - \
  "$status_json" \
  "$servers_json" \
  "$plan_tsv" \
  "$AUDIT_FILE" \
  "$checked_at" <<'PY'
import json
import sys

status_path, servers_path, plan_path, audit_path, checked_at = sys.argv[1:6]

status = json.load(open(status_path, "r", encoding="utf-8-sig"))
servers = json.load(open(servers_path, "r", encoding="utf-8-sig"))

hosts = {}
for node in status.get("nodes", []):
    host = node.get("host")
    if not host:
        continue

    decision = node.get("decision") or {}
    placement = node.get("placement") or {}
    service = node.get("service") or {}
    conclusion = node.get("conclusion") or {}

    trusted = (
        conclusion.get("level") == "ok"
        and decision.get("result") == "PASS_FRESH"
        and placement.get("trait_present") is True
        and service.get("status") == "enabled"
        and service.get("state") == "up"
    )

    hosts[host] = {
        "trusted": trusted,
        "reason": decision.get("reason") or conclusion.get("code") or conclusion.get("text") or "UNKNOWN",
        "decision": decision.get("result") or "",
        "service_status": service.get("status") or "",
        "service_state": service.get("state") or "",
        "trait_present": placement.get("trait_present"),
    }

actions = []
for server in servers:
    server_id = server.get("ID") or server.get("id")
    name = server.get("Name") or server.get("name") or ""
    host = server.get("Host") or server.get("host") or ""
    vm_status = server.get("Status") or server.get("status") or ""

    if not server_id or not host:
        continue

    host_info = hosts.get(host)
    if not host_info:
        action = {
            "action": "MARK",
            "server_id": server_id,
            "server_name": name,
            "server_status": vm_status,
            "host": host,
            "reason": "HOST_NOT_IN_KEYLIME_MONITOR",
            "checked_at": checked_at,
        }
    elif host_info["trusted"]:
        action = {
            "action": "CLEAR",
            "server_id": server_id,
            "server_name": name,
            "server_status": vm_status,
            "host": host,
            "reason": "HOST_TRUSTED",
            "checked_at": checked_at,
        }
    else:
        action = {
            "action": "MARK",
            "server_id": server_id,
            "server_name": name,
            "server_status": vm_status,
            "host": host,
            "reason": host_info["reason"],
            "checked_at": checked_at,
        }
    actions.append(action)

with open(plan_path, "w", encoding="utf-8") as f:
    for item in actions:
        f.write("\t".join([
            item["action"],
            item["server_id"],
            item["host"],
            item["reason"],
            item["checked_at"],
        ]) + "\n")

audit = {
    "checked_at_utc": checked_at,
    "summary": status.get("summary"),
    "hosts": hosts,
    "actions": actions,
}
with open(audit_path, "w", encoding="utf-8") as f:
    json.dump(audit, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")

print("planned_actions=%d" % len(actions))
print("audit_file=%s" % audit_path)
PY

marked=0
cleared=0
failed=0

while IFS=$'\t' read -r action server_id host reason checked_at_utc; do
  [ -z "${action:-}" ] && continue

  if [ "$action" = "MARK" ]; then
    if openstack server set \
      --property keylime_trust_state=host_untrusted \
      --property keylime_trust_host="$host" \
      --property keylime_trust_reason="$reason" \
      --property keylime_trust_checked_at="$checked_at_utc" \
      "$server_id"; then
      marked=$((marked + 1))
    else
      failed=$((failed + 1))
    fi
  elif [ "$action" = "CLEAR" ]; then
    if openstack server unset \
      --property keylime_trust_state \
      --property keylime_trust_host \
      --property keylime_trust_reason \
      --property keylime_trust_checked_at \
      "$server_id" 2>/dev/null; then
      cleared=$((cleared + 1))
    else
      # Missing properties are acceptable during cleanup.
      cleared=$((cleared + 1))
    fi
  fi
done < "$plan_tsv"

echo "marked=$marked"
echo "cleared=$cleared"
echo "failed=$failed"
echo "audit_file=$AUDIT_FILE"
