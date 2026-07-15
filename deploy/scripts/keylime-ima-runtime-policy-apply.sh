#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-ima-runtime-policy-apply.sh <host|all> <policy_id|bound>

Examples:
  keylime-ima-runtime-policy-apply.sh all bound
  keylime-ima-runtime-policy-apply.sh csri8 csri8-runtime-guard
EOF
}

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"

[ -r "$ENV_FILE" ] && source "$ENV_FILE"
[ -r "$OPENRC" ] && source "$OPENRC"

TARGET="${1:-all}"
POLICY_ID="${2:-bound}"

if [ "$TARGET" = "-h" ] || [ "$TARGET" = "--help" ]; then
  usage
  exit 0
fi

KEYLIME_DIR="${KEYLIME_DIR:-/opt/keylime-docker}"
STATE_DIR="${KEYLIME_OPENSTACK_STATE_DIR:-/var/lib/keylime-openstack-sync}"
STORE_FILE="${KEYLIME_PCR_POLICY_FILE:-$STATE_DIR/tpm-pcr-policies.json}"
AUDIT_FILE="${KEYLIME_RUNTIME_POLICY_APPLY_AUDIT_FILE:-/var/log/keylime-openstack-runtime-policy-apply.json}"
AGENT_PORT="${KEYLIME_AGENT_PORT:-9002}"
AGENT_API_VERSION="${KEYLIME_AGENT_API_VERSION:-2.5}"

VERIFIER_IP="${KEYLIME_VERIFIER_IP:-172.31.100.10}"
VERIFIER_PORT="${KEYLIME_VERIFIER_PORT:-8881}"
REGISTRAR_IP="${KEYLIME_REGISTRAR_IP:-172.31.100.10}"
REGISTRAR_PORT="${KEYLIME_REGISTRAR_PORT:-8891}"
RUN_SYNC="${KEYLIME_RUNTIME_POLICY_APPLY_SYNC_NOW:-true}"
SYNC_MODE="${KEYLIME_RUNTIME_POLICY_APPLY_SYNC_MODE:-api}"
API_URL="${KEYLIME_OPENSTACK_API_URL:-http://127.0.0.1:8088}"
API_ENV_FILE="${KEYLIME_OPENSTACK_API_ENV_FILE:-/etc/keylime-openstack/keylime-openstack.env}"
INCLUDE_BOUND_BOOT="${KEYLIME_RUNTIME_POLICY_INCLUDE_BOUND_BOOT:-true}"
ADD_IF_MISSING="${KEYLIME_RUNTIME_POLICY_APPLY_ADD_IF_MISSING:-true}"
REPLACE_ON_CONFLICT="${KEYLIME_RUNTIME_POLICY_APPLY_REPLACE_ON_CONFLICT:-false}"

test -d "$KEYLIME_DIR"
test -r "$STORE_FILE"
mkdir -p "$(dirname "$AUDIT_FILE")"

parse_map_value() {
  local map="$1"
  local key="$2"
  echo "$map" | tr ',' '\n' | awk -F= -v k="$key" '$1 == k {print $2; exit}'
}

hosts=()
if [ "$TARGET" = "all" ]; then
  IFS=',' read -r -a hosts <<< "${KEYLIME_AGENT_HOSTS:-}"
else
  IFS=',' read -r -a hosts <<< "$TARGET"
fi

resolved_hosts=()
for host in "${hosts[@]}"; do
  host="${host//[[:space:]]/}"
  [ -n "$host" ] && resolved_hosts+=("$host")
done
hosts=("${resolved_hosts[@]}")
if [ "${#hosts[@]}" -eq 0 ]; then
  echo "ERROR: no target hosts resolved"
  exit 1
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
echo '{"applied":[],"failed":[]}' > "$tmp"

for host in "${hosts[@]}"; do
  uuid="$(parse_map_value "${KEYLIME_AGENT_UUID_MAP:-}" "$host")"
  ip="$(parse_map_value "${KEYLIME_AGENT_IP_MAP:-}" "$host")"

  mapfile -t policy_lines < <(
    python3 - "$STORE_FILE" "$host" "$POLICY_ID" <<'PY'
import json
import sys

store_path, host, requested_policy_id = sys.argv[1:4]
store = json.load(open(store_path, encoding="utf-8"))

def runtime_binding(bindings, host):
    binding = bindings.get(host, {})
    if isinstance(binding, dict) and isinstance(binding.get("runtime"), dict):
        return binding["runtime"]
    return {}

def boot_binding(bindings, host):
    binding = bindings.get(host, {})
    if isinstance(binding, dict) and isinstance(binding.get("boot"), dict):
        return binding["boot"]
    if isinstance(binding, dict) and binding.get("policy_id"):
        return binding
    return {}

def find_policy(store, policy_id):
    for item in store.get("policies", []):
        if isinstance(item, dict) and item.get("id") == policy_id:
            return item
    return None

policy_id = requested_policy_id
if requested_policy_id == "bound":
    policy_id = str(runtime_binding(store.get("bindings", {}), host).get("policy_id", ""))

policy = find_policy(store, policy_id)
boot_policy_id = str(boot_binding(store.get("bindings", {}), host).get("policy_id", ""))
boot_policy = find_policy(store, boot_policy_id) if boot_policy_id else None
boot_tpm_policy = ""
boot_policy_name = ""
if boot_policy and boot_policy.get("type") == "tpm_pcr":
    boot_policy_name = str(boot_policy.get("name", ""))
    tpm_policy = dict(boot_policy.get("tpm_policy") or {})
    tpm_policy.pop("mask", None)
    if tpm_policy:
        boot_tpm_policy = json.dumps(tpm_policy, separators=(",", ":"), sort_keys=True)

if not policy or policy.get("type") != "ima_runtime":
    for _ in range(7):
        print("")
else:
    print(policy.get("id", ""))
    print(policy.get("runtime_policy_name") or policy.get("id", ""))
    print(policy.get("runtime_policy_path", ""))
    print(policy.get("name", ""))
    print(boot_policy_id)
    print(boot_policy_name)
    print(boot_tpm_policy)
PY
  )

  selected_policy_id="${policy_lines[0]:-}"
  runtime_policy_name="${policy_lines[1]:-}"
  runtime_policy_path="${policy_lines[2]:-}"
  policy_name="${policy_lines[3]:-}"
  boot_policy_id="${policy_lines[4]:-}"
  boot_policy_name="${policy_lines[5]:-}"
  boot_tpm_policy_json="${policy_lines[6]:-}"

  if [ -z "$uuid" ] || [ -z "$ip" ] || [ -z "$selected_policy_id" ] || [ -z "$runtime_policy_path" ] || [ ! -r "$runtime_policy_path" ]; then
    echo "WARN: skip $host because uuid/ip/runtime policy is missing"
    python3 - "$tmp" "$host" "$selected_policy_id" "$runtime_policy_path" <<'PY'
import json
import sys
p, host, policy_id, runtime_policy_path = sys.argv[1:]
d = json.load(open(p))
d["failed"].append({
    "host": host,
    "policy_id": policy_id,
    "runtime_policy_path": runtime_policy_path,
    "reason": "missing_uuid_ip_or_runtime_policy",
})
json.dump(d, open(p, "w"), indent=2)
PY
    continue
  fi

  runtime_policy_dir="$(dirname "$runtime_policy_path")"
  runtime_policy_file="$(basename "$runtime_policy_path")"
  runtime_container_path="/keylime-runtime-policy/$runtime_policy_file"

  echo "=== Apply IMA runtime policy to $host ==="
  echo "ip=$ip uuid=$uuid policy_id=$selected_policy_id runtime_policy=$runtime_policy_path"
  boot_tpm_policy_args=()
  bound_boot_policy_included=false
  include_bound_boot=false
  case "${INCLUDE_BOUND_BOOT,,}" in
    1|true|yes|y|on) include_bound_boot=true ;;
  esac
  if [ "$include_bound_boot" = "true" ] && [ -n "$boot_tpm_policy_json" ]; then
    echo "include_bound_boot_policy=$boot_policy_id"
    boot_tpm_policy_args=(--tpm_policy "$boot_tpm_policy_json")
    bound_boot_policy_included=true
  elif [ "$include_bound_boot" = "true" ]; then
    echo "WARN: no bound boot TPM policy found for $host; runtime update will not carry PCR7 policy."
  fi
  tenant_policy_args=(
    -t "$ip"
    -tp "$AGENT_PORT"
    -u "$uuid"
    -v "$VERIFIER_IP"
    -vp "$VERIFIER_PORT"
    -r "$REGISTRAR_IP"
    -rp "$REGISTRAR_PORT"
    --agent-api-version "$AGENT_API_VERSION"
    --runtime-policy-name "$runtime_policy_name"
    --runtime-policy "$runtime_container_path"
    "${boot_tpm_policy_args[@]}"
  )

  set +e
  (
    cd "$KEYLIME_DIR"
    docker compose run --rm \
      -v "$runtime_policy_dir:/keylime-runtime-policy:ro" \
      keylime-tenant \
      -c update \
      "${tenant_policy_args[@]}"
  )
  update_rc=$?
  add_rc=0
  delete_rc=0

  include_add_if_missing=false
  case "${ADD_IF_MISSING,,}" in
    1|true|yes|y|on) include_add_if_missing=true ;;
  esac
  if [ "$update_rc" -ne 0 ] && [ "$include_add_if_missing" = "true" ]; then
    echo "WARN: runtime update failed for $host rc=$update_rc; try tenant add for verifier enrollment."
    (
      cd "$KEYLIME_DIR"
      docker compose run --rm \
        -v "$runtime_policy_dir:/keylime-runtime-policy:ro" \
        keylime-tenant \
        -c add \
        "${tenant_policy_args[@]}"
    )
    add_rc=$?
    if [ "$add_rc" -eq 0 ]; then
      update_rc=0
    fi
  fi

  include_replace_on_conflict=false
  case "${REPLACE_ON_CONFLICT,,}" in
    1|true|yes|y|on) include_replace_on_conflict=true ;;
  esac
  if [ "$update_rc" -ne 0 ] && [ "$add_rc" -ne 0 ] && [ "$include_replace_on_conflict" = "true" ]; then
    echo "WARN: runtime update/add failed for $host; replace verifier enrollment with delete + add."
    (
      cd "$KEYLIME_DIR"
      docker compose run --rm keylime-tenant \
        -c delete \
        -u "$uuid" \
        -v "$VERIFIER_IP" \
        -vp "$VERIFIER_PORT" \
        -r "$REGISTRAR_IP" \
        -rp "$REGISTRAR_PORT"
    )
    delete_rc=$?
    if [ "$delete_rc" -eq 0 ]; then
      (
        cd "$KEYLIME_DIR"
        docker compose run --rm \
          -v "$runtime_policy_dir:/keylime-runtime-policy:ro" \
          keylime-tenant \
          -c add \
          "${tenant_policy_args[@]}"
      )
      add_rc=$?
      if [ "$add_rc" -eq 0 ]; then
        update_rc=0
      fi
    fi
  fi

  reactivate_rc=0
  if [ "$update_rc" -eq 0 ]; then
    (
      cd "$KEYLIME_DIR"
      docker compose run --rm keylime-tenant \
        -c reactivate \
        -u "$uuid" \
        -v "$VERIFIER_IP" \
        -vp "$VERIFIER_PORT" \
        -r "$REGISTRAR_IP" \
        -rp "$REGISTRAR_PORT"
    )
    reactivate_rc=$?
  fi
  set -e

  python3 - "$tmp" "$host" "$ip" "$uuid" "$selected_policy_id" "$policy_name" "$runtime_policy_name" "$runtime_policy_path" "$boot_policy_id" "$boot_policy_name" "$bound_boot_policy_included" "$update_rc" "$add_rc" "$delete_rc" "$reactivate_rc" <<'PY'
import json
import sys
p, host, ip, uuid, policy_id, policy_name, runtime_policy_name, runtime_policy_path, boot_policy_id, boot_policy_name, bound_boot_policy_included, update_rc, add_rc, delete_rc, reactivate_rc = sys.argv[1:]
d = json.load(open(p))
item = {
    "host": host,
    "ip": ip,
    "uuid": uuid,
    "policy_id": policy_id,
    "policy_name": policy_name,
    "runtime_policy_name": runtime_policy_name,
    "runtime_policy_path": runtime_policy_path,
    "bound_boot_policy_id": boot_policy_id,
    "bound_boot_policy_name": boot_policy_name,
    "bound_boot_policy_included": bound_boot_policy_included == "true",
    "update_rc": int(update_rc),
    "add_rc": int(add_rc),
    "delete_rc": int(delete_rc),
    "reactivate_rc": int(reactivate_rc),
}
if int(update_rc) == 0 and int(reactivate_rc) == 0:
    d["applied"].append(item)
else:
    item["reason"] = "keylime_runtime_update_or_reactivate_failed"
    d["failed"].append(item)
json.dump(d, open(p, "w"), indent=2)
PY
done

python3 - "$tmp" "$AUDIT_FILE" "$TARGET" "$POLICY_ID" <<'PY'
import json
import sys
from datetime import datetime, timezone
src, dst, target, policy_id = sys.argv[1:]
d = json.load(open(src))
d["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
d["target"] = target
d["requested_policy_id"] = policy_id
d["policy_module"] = "runtime"
json.dump(d, open(dst, "w"), indent=2, sort_keys=True)
open(dst, "a").write("\n")
PY

python3 -m json.tool "$AUDIT_FILE"

if [ "$RUN_SYNC" = "true" ]; then
  echo "=== Force trust sync after runtime policy apply ==="
  case "$SYNC_MODE" in
    api|fastapi)
      admin_token="${ADMIN_TOKEN:-}"
      if [ -z "$admin_token" ] && [ -r "$API_ENV_FILE" ]; then
        admin_token="$(grep '^ADMIN_TOKEN=' "$API_ENV_FILE" | cut -d= -f2- || true)"
      fi
      if [ -n "$admin_token" ] && command -v curl >/dev/null 2>&1; then
        curl -fsS -X POST -H "X-Admin-Token: $admin_token" "$API_URL/api/tasks/sync" || true
      else
        echo "WARN: cannot call FastAPI sync; set ADMIN_TOKEN or KEYLIME_OPENSTACK_API_ENV_FILE." >&2
      fi
      ;;
    control-loop)
      systemctl start keylime-openstack-sync.service || true
      ;;
    script)
      "${KEYLIME_OPENSTACK_SYNC_DIR:-/opt/keylime-openstack-sync}/keylime-placement-sync.sh" || true
      ;;
    none|false|off|0)
      echo "sync_skipped=true"
      ;;
    *)
      echo "WARN: unknown KEYLIME_RUNTIME_POLICY_APPLY_SYNC_MODE=$SYNC_MODE" >&2
      ;;
  esac
fi

failed_count="$(python3 - "$AUDIT_FILE" <<'PY'
import json
import sys
d = json.load(open(sys.argv[1]))
print(len(d.get("failed", [])))
PY
)"

if [ "$failed_count" != "0" ]; then
  echo "ERROR: one or more runtime policy apply actions failed"
  exit 1
fi
