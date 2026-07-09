#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -r "$ENV_FILE" ] && source "$ENV_FILE"

BASELINE="${1:-${KEYLIME_TPM_EVIDENCE_BASELINE_JSON:-/var/log/keylime-openstack-tpm-evidence-baseline.json}}"
STATE_DIR="${KEYLIME_OPENSTACK_STATE_DIR:-/var/lib/keylime-openstack-sync}"
POLICY_BASE="${KEYLIME_POLICY_BASE_DIR:-$STATE_DIR/policies}"
STORE_FILE="${KEYLIME_PCR_POLICY_FILE:-$STATE_DIR/tpm-pcr-policies.json}"
PROFILE_NAME="${KEYLIME_POLICY_PROFILE_NAME:-csri-lab-pcr-policy-profile}"
AUDIT_FILE="${KEYLIME_POLICY_RENDER_AUDIT_FILE:-/var/log/keylime-openstack-policy-render.json}"
BIND_MODE="${KEYLIME_POLICY_RENDER_BIND_MODE:-pcr7}"
CREATE_BAD_PCR7="${KEYLIME_POLICY_RENDER_CREATE_BAD_PCR7:-false}"

test -r "$BASELINE"

mkdir -p "$POLICY_BASE/profiles" "$POLICY_BASE/rendered" "$POLICY_BASE/history" "$(dirname "$STORE_FILE")"

python3 - "$BASELINE" "$POLICY_BASE" "$STORE_FILE" "$PROFILE_NAME" "$AUDIT_FILE" "$BIND_MODE" "$CREATE_BAD_PCR7" <<'PY'
import json
import os
import shutil
import sys
from datetime import datetime, timezone

baseline_path, policy_base, store_file, profile_name, audit_file, bind_mode, create_bad = sys.argv[1:]
baseline = json.load(open(baseline_path, encoding="utf-8"))
ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def calc_mask(pcrs):
    mask = 0
    for key in pcrs:
        mask |= 1 << int(key)
    return hex(mask)

def tpm_policy(pcrs):
    out = {"mask": calc_mask(pcrs)}
    for key in sorted(pcrs, key=lambda item: int(item)):
        out[key] = [pcrs[key].lower()]
    return out

def make_policy(policy_id, name, description, pcrs, source, module="boot_measurement"):
    return {
        "id": policy_id,
        "name": name,
        "description": description,
        "type": "tpm_pcr",
        "module": module,
        "hash_alg": "sha256",
        "pcrs": {str(k): str(v).upper() for k, v in pcrs.items()},
        "mask": calc_mask(pcrs),
        "tpm_policy": tpm_policy(pcrs),
        "source": source,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }

def load_store(path):
    if not os.path.exists(path):
        return {"version": 1, "policies": [], "bindings": {}, "events": []}
    try:
        store = json.load(open(path, encoding="utf-8"))
    except json.JSONDecodeError:
        backup = f"{path}.invalid.{ts}"
        shutil.copyfile(path, backup)
        return {"version": 1, "policies": [], "bindings": {}, "events": [{"at_utc": ts, "action": "invalid_store_backed_up", "path": backup}]}
    store.setdefault("version", 1)
    store.setdefault("policies", [])
    store.setdefault("bindings", {})
    store.setdefault("events", [])
    return store

def upsert_policy(store, policy):
    policies = []
    replaced = False
    for item in store.get("policies", []):
        if isinstance(item, dict) and item.get("id") == policy["id"]:
            created = item.get("created_at_utc")
            if created:
                policy["created_at_utc"] = created
            policies.append(policy)
            replaced = True
        else:
            policies.append(item)
    if not replaced:
        policies.append(policy)
    policies.sort(key=lambda item: str(item.get("id", "")))
    store["policies"] = policies

def set_boot_binding(bindings, host, record):
    current = bindings.get(host, {})
    if isinstance(current, dict) and current.get("policy_id") and not current.get("boot"):
        current = {"boot": current}
    if not isinstance(current, dict):
        current = {}
    current["boot"] = record
    bindings[host] = current

store = load_store(store_file)
profile = {
    "name": profile_name,
    "type": "tpm_pcr",
    "bank": "sha256",
    "source_baseline": baseline_path,
    "source_checked_at_utc": baseline.get("checked_at_utc"),
    "rendered_at_utc": ts,
    "render_mode": "pcr7-and-per-node-pcr0-7",
    "hosts": {},
}
audit = {
    "profile": profile_name,
    "rendered_at_utc": ts,
    "baseline": baseline_path,
    "store_file": store_file,
    "bind_mode": bind_mode,
    "rendered": [],
    "warnings": [],
}

for node in baseline.get("nodes", []):
    host = node.get("host")
    ip = node.get("ip")
    values = {str(k): str(v).upper() for k, v in node.get("sha256_pcr_0_7", {}).items()}
    missing = [str(i) for i in range(8) if not values.get(str(i))]
    if not host or missing:
        audit["warnings"].append({"host": host, "warning": "missing_pcr_values", "missing": missing})
        continue

    pcr7 = {"7": values["7"]}
    pcr0_7 = {str(i): values[str(i)] for i in range(8)}
    pcr7_id = f"{host}-sha256-pcr7-baseline"
    pcr0_7_id = f"{host}-sha256-pcr0-7-exact"

    pcr7_policy = make_policy(
        pcr7_id,
        f"{host} SHA256 PCR7 baseline",
        f"Generated from TPM evidence baseline {baseline.get('checked_at_utc')}. Stable boot-measurement policy.",
        pcr7,
        {"baseline": baseline_path, "host": host, "mode": "pcr7"},
        module="boot_measurement",
    )
    pcr0_7_policy = make_policy(
        pcr0_7_id,
        f"{host} SHA256 PCR0-7 exact baseline",
        "Generated from TPM evidence baseline for controlled boot-measurement rollout.",
        pcr0_7,
        {"baseline": baseline_path, "host": host, "mode": "pcr0-7"},
        module="boot_measurement_diagnostic",
    )

    upsert_policy(store, pcr7_policy)
    upsert_policy(store, pcr0_7_policy)

    rendered_pcr7 = os.path.join(policy_base, "rendered", f"{host}.pcr7.tpm_policy.json")
    rendered_pcr0_7 = os.path.join(policy_base, "rendered", f"{host}.pcr0-7.tpm_policy.json")
    history_pcr7 = os.path.join(policy_base, "history", f"{ts}-{host}.pcr7.tpm_policy.json")
    history_pcr0_7 = os.path.join(policy_base, "history", f"{ts}-{host}.pcr0-7.tpm_policy.json")

    for path, policy in ((rendered_pcr7, pcr7_policy["tpm_policy"]), (rendered_pcr0_7, pcr0_7_policy["tpm_policy"])):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(policy, f, indent=2, sort_keys=True)
            f.write("\n")
    shutil.copyfile(rendered_pcr7, history_pcr7)
    shutil.copyfile(rendered_pcr0_7, history_pcr0_7)

    if bind_mode in ("pcr7", "pcr0-7"):
        bind_policy = pcr7_policy if bind_mode == "pcr7" else pcr0_7_policy
        set_boot_binding(store.setdefault("bindings", {}), host, {
            "host": host,
            "policy_id": bind_policy["id"],
            "policy_name": bind_policy["name"],
            "binding_mode": bind_mode,
            "bound_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": "render-from-baseline",
        })

    profile["hosts"][host] = {
        "ip": ip,
        "pcr7_policy_id": pcr7_id,
        "pcr0_7_policy_id": pcr0_7_id,
        "pcr7_policy_file": rendered_pcr7,
        "pcr0_7_policy_file": rendered_pcr0_7,
    }
    audit["rendered"].append(profile["hosts"][host] | {"host": host})

if create_bad.lower() in ("1", "true", "yes", "on"):
    bad_policy = make_policy(
        "bad-sha256-pcr7-zero",
        "BAD SHA256 PCR7 all-zero negative test",
        "Negative-test policy. Apply only to one selected host and restore immediately after validation.",
        {"7": "0" * 64},
        {"mode": "negative-test", "case": "Case 10A"},
        module="boot_measurement_negative_test",
    )
    upsert_policy(store, bad_policy)
    audit["rendered"].append({"host": "*", "pcr7_negative_policy_id": bad_policy["id"]})

profile_path = os.path.join(policy_base, "profiles", f"{profile_name}.json")
with open(profile_path, "w", encoding="utf-8") as f:
    json.dump(profile, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")
audit["profile_file"] = profile_path

store.setdefault("events", []).append({
    "at_utc": datetime.now(timezone.utc).isoformat(),
    "action": "policies_rendered_from_tpm_baseline",
    "baseline": baseline_path,
    "profile": profile_name,
    "bind_mode": bind_mode,
})
store["events"] = store.get("events", [])[-120:]
with open(store_file, "w", encoding="utf-8") as f:
    json.dump(store, f, ensure_ascii=False, indent=2, sort_keys=True)
    f.write("\n")

with open(audit_file, "w", encoding="utf-8") as f:
    json.dump(audit, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

echo "profile_file=$POLICY_BASE/profiles/$PROFILE_NAME.json"
echo "store_file=$STORE_FILE"
echo "audit_file=$AUDIT_FILE"
python3 -m json.tool "$AUDIT_FILE"
