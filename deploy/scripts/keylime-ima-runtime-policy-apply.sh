#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-ima-runtime-policy-apply.sh <host|all> <policy_id|bound>

Examples:
  keylime-ima-runtime-policy-apply.sh all bound
  keylime-ima-runtime-policy-apply.sh csri8 csri8-runtime-guard

Environment:
  KEYLIME_RUNTIME_POLICY_APPLY_NAME_MODE=unique|content-hash|stable
      unique is the default. It avoids Keylime verifier allowlists.name
      conflicts by giving each verifier apply attempt a unique runtime policy
      name while keeping the control-plane policy_id stable.
  KEYLIME_RUNTIME_POLICY_APPLY_SYNC_DELAY_SECONDS=15
      wait before the post-apply sync so Keylime can finish the next
      attestation after tenant update/reactivate.
  KEYLIME_RUNTIME_POLICY_APPLY_FORCE_REPLACE=false
      delete the verifier enrollment before adding the runtime policy. Use this
      when Keylime keeps failing with stale verifier state such as
      ima.validation.ima-ng.runtime_policy_hash after a normal update.
EOF
}

DEFAULT_ENV_FILE="/etc/keylime-openstack/keylime-openstack.env"
LEGACY_ENV_FILE="/etc/keylime-openstack-sync/openstack-keylime-lab.env"
ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-}"
if [ -z "$ENV_FILE" ]; then
  if [ -r "$DEFAULT_ENV_FILE" ]; then
    ENV_FILE="$DEFAULT_ENV_FILE"
  else
    ENV_FILE="$LEGACY_ENV_FILE"
  fi
fi
OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"

[ -r "$ENV_FILE" ] && source "$ENV_FILE"
[ -r "$OPENRC" ] && source "$OPENRC"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -r "$SCRIPT_DIR/keylime-script-lib.sh" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/keylime-script-lib.sh"
  load_keylime_agent_inventory
fi

TARGET="${1:-all}"
POLICY_ID="${2:-bound}"

if [ "$TARGET" = "-h" ] || [ "$TARGET" = "--help" ]; then
  usage
  exit 0
fi

KEYLIME_DIR="${KEYLIME_DIR:-${KEYLIME_DOCKER_DIR:-/opt/keylime-docker}}"
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
SYNC_DELAY_SECONDS="${KEYLIME_RUNTIME_POLICY_APPLY_SYNC_DELAY_SECONDS:-15}"
API_URL="${KEYLIME_OPENSTACK_API_URL:-http://127.0.0.1:8088}"
API_ENV_FILE="${KEYLIME_OPENSTACK_API_ENV_FILE:-/etc/keylime-openstack/keylime-openstack.env}"
INCLUDE_BOUND_BOOT="${KEYLIME_RUNTIME_POLICY_INCLUDE_BOUND_BOOT:-true}"
ADD_IF_MISSING="${KEYLIME_RUNTIME_POLICY_APPLY_ADD_IF_MISSING:-true}"
REPLACE_ON_CONFLICT="${KEYLIME_RUNTIME_POLICY_APPLY_REPLACE_ON_CONFLICT:-false}"
FORCE_REPLACE="${KEYLIME_RUNTIME_POLICY_APPLY_FORCE_REPLACE:-false}"
APPLY_NAME_MODE="${KEYLIME_RUNTIME_POLICY_APPLY_NAME_MODE:-unique}"
APPLY_NAME_OVERRIDE="${KEYLIME_RUNTIME_POLICY_APPLY_NAME:-}"

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
  if command -v keylime_resolve_agent_hosts >/dev/null 2>&1; then
    IFS=',' read -r -a hosts <<< "$(keylime_resolve_agent_hosts "$TARGET")"
  else
    IFS=',' read -r -a hosts <<< "${KEYLIME_AGENT_HOSTS:-}"
  fi
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
  if command -v keylime_resolve_agent_uuid >/dev/null 2>&1; then
    uuid="$(keylime_resolve_agent_uuid "$host")"
  else
    uuid="$(parse_map_value "${KEYLIME_AGENT_UUID_MAP:-}" "$host")"
  fi
  if command -v keylime_resolve_agent_ip >/dev/null 2>&1; then
    ip="$(keylime_resolve_agent_ip "$host")"
  else
    ip="$(parse_map_value "${KEYLIME_AGENT_IP_MAP:-}" "$host")"
  fi

  mapfile -t policy_lines < <(
    python3 - "$STORE_FILE" "$host" "$POLICY_ID" <<'PY'
import json
import re
import sys

store_path, host, requested_policy_id = sys.argv[1:4]
store = json.load(open(store_path, encoding="utf-8"))

def slugify(value):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug or "ima-runtime-policy"

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
else:
    policy_id = slugify(policy_id)

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
  runtime_policy_sha="$(
    python3 - "$runtime_policy_path" <<'PY'
import hashlib
import sys
print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())
PY
  )"
  runtime_policy_sha_short="${runtime_policy_sha:0:12}"
  apply_suffix="$(date -u +%Y%m%d%H%M%S)-$$"
  keylime_runtime_policy_name="$runtime_policy_name"
  if [ -n "$APPLY_NAME_OVERRIDE" ]; then
    keylime_runtime_policy_name="$APPLY_NAME_OVERRIDE"
  else
    case "${APPLY_NAME_MODE,,}" in
      unique)
        keylime_runtime_policy_name="${runtime_policy_name}-${runtime_policy_sha_short}-${apply_suffix}"
        ;;
      content-hash)
        keylime_runtime_policy_name="${runtime_policy_name}-${runtime_policy_sha_short}"
        ;;
      stable)
        keylime_runtime_policy_name="$runtime_policy_name"
        ;;
      *)
        echo "WARN: unknown KEYLIME_RUNTIME_POLICY_APPLY_NAME_MODE=$APPLY_NAME_MODE; using unique" >&2
        keylime_runtime_policy_name="${runtime_policy_name}-${runtime_policy_sha_short}-${apply_suffix}"
        ;;
    esac
  fi

  echo "=== Apply IMA runtime policy to $host ==="
  echo "ip=$ip uuid=$uuid policy_id=$selected_policy_id runtime_policy=$runtime_policy_path"
  echo "keylime_runtime_policy_name=$keylime_runtime_policy_name"
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
    --runtime-policy-name "$keylime_runtime_policy_name"
    --runtime-policy "$runtime_container_path"
    "${boot_tpm_policy_args[@]}"
  )

  add_rc=0
  delete_rc=0
  update_rc=0

  include_force_replace=false
  case "${FORCE_REPLACE,,}" in
    1|true|yes|y|on) include_force_replace=true ;;
  esac

  include_add_if_missing=false
  case "${ADD_IF_MISSING,,}" in
    1|true|yes|y|on) include_add_if_missing=true ;;
  esac

  set +e
  if [ "$include_force_replace" = "true" ]; then
    echo "force_replace=true; delete verifier enrollment before add"
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
    if [ "$delete_rc" -ne 0 ]; then
      echo "WARN: verifier delete returned rc=$delete_rc; continuing with add."
    fi
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
    else
      update_rc=1
    fi
  else
    (
      cd "$KEYLIME_DIR"
      docker compose run --rm \
        -v "$runtime_policy_dir:/keylime-runtime-policy:ro" \
        keylime-tenant \
        -c update \
        "${tenant_policy_args[@]}"
    )
    update_rc=$?

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

  python3 - "$tmp" "$host" "$ip" "$uuid" "$selected_policy_id" "$policy_name" "$runtime_policy_name" "$keylime_runtime_policy_name" "$runtime_policy_path" "$runtime_policy_sha" "$boot_policy_id" "$boot_policy_name" "$bound_boot_policy_included" "$update_rc" "$add_rc" "$delete_rc" "$reactivate_rc" <<'PY'
import json
import sys
p, host, ip, uuid, policy_id, policy_name, runtime_policy_name, keylime_runtime_policy_name, runtime_policy_path, runtime_policy_sha, boot_policy_id, boot_policy_name, bound_boot_policy_included, update_rc, add_rc, delete_rc, reactivate_rc = sys.argv[1:]
d = json.load(open(p))
item = {
    "host": host,
    "ip": ip,
    "uuid": uuid,
    "policy_id": policy_id,
    "policy_name": policy_name,
    "runtime_policy_name": runtime_policy_name,
    "keylime_runtime_policy_name": keylime_runtime_policy_name,
    "runtime_policy_path": runtime_policy_path,
    "runtime_policy_sha256": runtime_policy_sha,
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

if [ "$RUN_SYNC" = "true" ]; then
  case "$SYNC_DELAY_SECONDS" in
    ""|0|false|off|none)
      ;;
    *)
      echo "wait_before_sync_seconds=$SYNC_DELAY_SECONDS"
      sleep "$SYNC_DELAY_SECONDS"
      ;;
  esac

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
