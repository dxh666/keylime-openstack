#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-ima-runtime-policy-register.sh <host|all|host1,host2> <runtime_policy_json> [policy_id] [display_name]

Examples:
  keylime-ima-runtime-policy-register.sh csri8 /var/lib/keylime-openstack-sync/policies/runtime/csri8-runtime-policy.json
  keylime-ima-runtime-policy-register.sh all /var/lib/keylime-openstack-sync/policies/runtime/cloud-runtime-policy.json cloud-runtime-guard

This script does not synthesize Keylime runtime policy JSON. Generate the runtime policy with
the Keylime runtime policy tooling for your version, then register and bind it here.
EOF
}

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -r "$ENV_FILE" ] && source "$ENV_FILE"

TARGET="${1:-}"
RUNTIME_POLICY_SRC="${2:-}"
POLICY_ID="${3:-}"
DISPLAY_NAME="${4:-}"

if [ -z "$TARGET" ] || [ -z "$RUNTIME_POLICY_SRC" ]; then
  usage
  exit 1
fi

STATE_DIR="${KEYLIME_OPENSTACK_STATE_DIR:-/var/lib/keylime-openstack-sync}"
POLICY_BASE="${KEYLIME_POLICY_BASE_DIR:-$STATE_DIR/policies}"
STORE_FILE="${KEYLIME_PCR_POLICY_FILE:-$STATE_DIR/tpm-pcr-policies.json}"
AUDIT_FILE="${KEYLIME_RUNTIME_POLICY_REGISTER_AUDIT_FILE:-/var/log/keylime-openstack-runtime-policy-register.json}"
COPY_POLICY="${KEYLIME_RUNTIME_POLICY_COPY:-true}"
RUNTIME_POLICY_NAME="${KEYLIME_RUNTIME_POLICY_NAME:-}"
PROTECTED_PATHS="${KEYLIME_RUNTIME_PROTECTED_PATHS:-${KEYLIME_RUNTIME_GUARD_PATH:-/opt/keylime-cloud-integrity/cloud-runtime-guard.sh}}"
EXCLUDES="${KEYLIME_RUNTIME_EXCLUDES:-}"

test -r "$RUNTIME_POLICY_SRC"
mkdir -p "$POLICY_BASE/runtime" "$(dirname "$STORE_FILE")" "$(dirname "$AUDIT_FILE")"

python3 - \
  "$STORE_FILE" \
  "$POLICY_BASE" \
  "$AUDIT_FILE" \
  "$TARGET" \
  "$RUNTIME_POLICY_SRC" \
  "$POLICY_ID" \
  "$DISPLAY_NAME" \
  "${KEYLIME_AGENT_HOSTS:-}" \
  "$RUNTIME_POLICY_NAME" \
  "$PROTECTED_PATHS" \
  "$EXCLUDES" \
  "$COPY_POLICY" <<'PY'
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    store_file,
    policy_base,
    audit_file,
    target,
    runtime_policy_src,
    policy_id,
    display_name,
    agent_hosts_csv,
    runtime_policy_name,
    protected_paths_raw,
    excludes_raw,
    copy_policy,
) = sys.argv[1:]

def now():
    return datetime.now(timezone.utc).isoformat()

def slugify(value):
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug or "ima-runtime-policy"

def split_list(value):
    items = []
    for chunk in str(value or "").replace(",", "\n").splitlines():
        item = chunk.strip()
        if item:
            items.append(item)
    return items

def load_store(path):
    if not os.path.exists(path):
        return {"version": 1, "policies": [], "bindings": {}, "events": []}
    data = json.load(open(path, encoding="utf-8"))
    data.setdefault("version", 1)
    data.setdefault("policies", [])
    data.setdefault("bindings", {})
    data.setdefault("events", [])
    return data

def upsert_policy(store, policy):
    out = []
    replaced = False
    for item in store.get("policies", []):
        if isinstance(item, dict) and item.get("id") == policy["id"]:
            if item.get("created_at_utc"):
                policy["created_at_utc"] = item["created_at_utc"]
            out.append(policy)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(policy)
    out.sort(key=lambda item: str(item.get("id", "")))
    store["policies"] = out

def set_runtime_binding(bindings, host, policy):
    current = bindings.get(host, {})
    if isinstance(current, dict) and current.get("policy_id") and not current.get("boot"):
        current = {"boot": current}
    if not isinstance(current, dict):
        current = {}
    current["runtime"] = {
        "host": host,
        "policy_id": policy["id"],
        "policy_name": policy["name"],
        "policy_type": policy["type"],
        "binding_mode": "runtime",
        "bound_at_utc": now(),
        "source": "runtime-policy-register",
    }
    bindings[host] = current

src_path = Path(runtime_policy_src).resolve()
runtime_json = json.load(open(src_path, encoding="utf-8"))
digest = hashlib.sha256(src_path.read_bytes()).hexdigest()
policy_id = slugify(policy_id or src_path.stem)
display_name = display_name or f"{policy_id} IMA runtime policy"
runtime_policy_name = runtime_policy_name or policy_id

runtime_dir = Path(policy_base) / "runtime"
runtime_dir.mkdir(parents=True, exist_ok=True)
if str(copy_policy).strip().lower() in ("1", "true", "yes", "on"):
    dst_path = runtime_dir / f"{policy_id}.json"
    if src_path != dst_path.resolve():
        shutil.copyfile(src_path, dst_path)
else:
    dst_path = src_path
digest = hashlib.sha256(dst_path.read_bytes()).hexdigest()

if target == "all":
    hosts = [item.strip() for item in agent_hosts_csv.split(",") if item.strip()]
else:
    hosts = [item.strip() for item in target.split(",") if item.strip()]
if not hosts:
    raise SystemExit("no target hosts resolved")

store = load_store(store_file)
policy = {
    "id": policy_id,
    "name": display_name,
    "description": "Registered Keylime IMA runtime policy JSON for PCR10 / runtime measurement enforcement.",
    "type": "ima_runtime",
    "module": "runtime_integrity",
    "runtime_policy_name": runtime_policy_name,
    "runtime_policy_path": str(dst_path),
    "protected_paths": split_list(protected_paths_raw),
    "excludes": split_list(excludes_raw),
    "source": {
        "registered_from": str(src_path),
        "sha256": digest,
        "json_top_level_keys": sorted(runtime_json.keys()) if isinstance(runtime_json, dict) else [],
    },
    "created_at_utc": now(),
    "updated_at_utc": now(),
}
upsert_policy(store, policy)
for host in hosts:
    set_runtime_binding(store.setdefault("bindings", {}), host, policy)

event = {
    "at_utc": now(),
    "action": "ima_runtime_policy_registered",
    "policy_id": policy["id"],
    "hosts": hosts,
    "runtime_policy_path": str(dst_path),
}
store.setdefault("events", []).append(event)
store["events"] = store.get("events", [])[-120:]

Path(store_file).parent.mkdir(parents=True, exist_ok=True)
with open(store_file, "w", encoding="utf-8") as f:
    json.dump(store, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")

audit = {
    "checked_at_utc": now(),
    "store_file": store_file,
    "policy_id": policy["id"],
    "policy_name": policy["name"],
    "runtime_policy_name": runtime_policy_name,
    "runtime_policy_path": str(dst_path),
    "source_path": str(src_path),
    "source_sha256": digest,
    "hosts": hosts,
    "registered": [{"host": host, "policy_id": policy["id"]} for host in hosts],
}
Path(audit_file).parent.mkdir(parents=True, exist_ok=True)
with open(audit_file, "w", encoding="utf-8") as f:
    json.dump(audit, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
PY

echo "store_file=$STORE_FILE"
echo "audit_file=$AUDIT_FILE"
python3 -m json.tool "$AUDIT_FILE"
