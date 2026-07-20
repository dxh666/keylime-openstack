#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-ima-runtime-policy-diff.sh <host> [policy_id|bound]

Examples:
  keylime-ima-runtime-policy-diff.sh hygon22
  keylime-ima-runtime-policy-diff.sh hygon22 bound
  keylime-ima-runtime-policy-diff.sh hygon22 hygon22-ima-current-20260715t101602z

Compare a compute host's live IMA ascii_runtime_measurements with the runtime
policy registered in the local Keylime/OpenStack policy store. This is a
read-only diagnostic helper for Keylime failures such as:

  ima.validation.ima-ng.runtime_policy_hash
  ima.validation.ima-ng.not_in_allowlist
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
[ -r "$ENV_FILE" ] && source "$ENV_FILE"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -r "$SCRIPT_DIR/keylime-script-lib.sh" ]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/keylime-script-lib.sh"
  load_keylime_agent_inventory
fi

HOST="${1:-}"
POLICY_ID="${2:-bound}"

if [ "$HOST" = "-h" ] || [ "$HOST" = "--help" ] || [ -z "$HOST" ]; then
  usage
  if [ -z "$HOST" ]; then
    exit 1
  fi
  exit 0
fi

STATE_DIR="${KEYLIME_OPENSTACK_STATE_DIR:-/var/lib/keylime-openstack-sync}"
STORE_FILE="${KEYLIME_PCR_POLICY_FILE:-$STATE_DIR/tpm-pcr-policies.json}"
POLICY_BASE="${KEYLIME_POLICY_BASE_DIR:-$STATE_DIR/policies}"
RUNTIME_DIR="$POLICY_BASE/runtime"
EVIDENCE_DIR="${KEYLIME_RUNTIME_POLICY_EVIDENCE_DIR:-$RUNTIME_DIR/evidence-clean}"
SSH_OPTS="${KEYLIME_RUNTIME_POLICY_SSH_OPTS:--o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=no}"
AGENT_IP_OVERRIDE="${KEYLIME_RUNTIME_POLICY_HOST_IP:-}"
MAX_PRINT="${KEYLIME_RUNTIME_POLICY_DIFF_MAX_PRINT:-80}"

HOST_IP="$AGENT_IP_OVERRIDE"
if [ -z "$HOST_IP" ]; then
  if command -v keylime_resolve_agent_ip >/dev/null 2>&1; then
    HOST_IP="$(keylime_resolve_agent_ip "$HOST")"
  else
    HOST_IP="$(echo "${KEYLIME_AGENT_IP_MAP:-}" | tr ',' '\n' | awk -F= -v k="$HOST" '$1 == k {print $2; exit}')"
  fi
fi
if [ -z "$HOST_IP" ]; then
  echo "ERROR: cannot resolve IP for host '$HOST'. Set KEYLIME_AGENT_IP_MAP, KEYLIME_RUNTIME_POLICY_HOST_IP, inventory file, or ensure /api/nodes is reachable." >&2
  exit 1
fi

test -r "$STORE_FILE"
mkdir -p "$EVIDENCE_DIR"

mapfile -t policy_lines < <(
  python3 - "$STORE_FILE" "$HOST" "$POLICY_ID" <<'PY'
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
if not policy or policy.get("type") != "ima_runtime":
    print("")
    print("")
    print("")
else:
    print(policy.get("id", ""))
    print(policy.get("runtime_policy_path", ""))
    print(policy.get("runtime_policy_name") or policy.get("id", ""))
PY
)

SELECTED_POLICY_ID="${policy_lines[0]:-}"
RUNTIME_POLICY_PATH="${policy_lines[1]:-}"
RUNTIME_POLICY_NAME="${policy_lines[2]:-}"
if [ -z "$SELECTED_POLICY_ID" ] || [ -z "$RUNTIME_POLICY_PATH" ] || [ ! -r "$RUNTIME_POLICY_PATH" ]; then
  echo "ERROR: cannot resolve readable runtime policy for host=$HOST policy=$POLICY_ID" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LIVE_FILE="$EVIDENCE_DIR/${HOST}-ima-diff-live-$TS.txt"
OUT_JSON="${KEYLIME_RUNTIME_POLICY_DIFF_JSON:-$RUNTIME_DIR/${HOST}-runtime-policy-diff.json}"

echo "host=$HOST"
echo "host_ip=$HOST_IP"
echo "policy_id=$SELECTED_POLICY_ID"
echo "runtime_policy_name=$RUNTIME_POLICY_NAME"
echo "runtime_policy=$RUNTIME_POLICY_PATH"
echo "live_ima_file=$LIVE_FILE"

# shellcheck disable=SC2086
ssh $SSH_OPTS "root@$HOST_IP" 'cat /sys/kernel/security/ima/ascii_runtime_measurements' > "$LIVE_FILE"

python3 - "$RUNTIME_POLICY_PATH" "$LIVE_FILE" "$OUT_JSON" "$MAX_PRINT" <<'PY'
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone

policy_path, live_path, out_path, max_print_raw = sys.argv[1:5]
max_print = int(max_print_raw)
policy = json.load(open(policy_path, encoding="utf-8"))

digests_raw = policy.get("digests") or {}
digests = {
    str(path): {str(item).lower() for item in values}
    for path, values in digests_raw.items()
    if isinstance(values, list)
}
exclude_patterns = [str(item) for item in policy.get("excludes") or []]
excludes = [re.compile(item) for item in exclude_patterns]

def parse_measurement(line):
    parts = line.strip().split()
    if len(parts) < 4:
        return None
    digest_index = None
    for idx, item in enumerate(parts):
        if re.match(r"^(sha1|sha256|sha384|sha512|sm3_256):[0-9a-fA-F]+$", item):
            digest_index = idx
            break
    if digest_index is None or digest_index + 1 >= len(parts):
        return None
    algo, digest = parts[digest_index].split(":", 1)
    return {
        "template": parts[2] if len(parts) > 2 else "",
        "algo": algo.lower(),
        "digest": digest.lower(),
        "path": parts[digest_index + 1],
        "line": line.rstrip("\n"),
    }

def excluded(path):
    return any(pattern.search(path) for pattern in excludes)

stats = Counter()
path_missing = []
hash_missing = []
malformed = []
excluded_rows = []

with open(live_path, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        if not line.strip():
            continue
        parsed = parse_measurement(line)
        if not parsed:
            stats["malformed"] += 1
            if len(malformed) < max_print:
                malformed.append(line.rstrip("\n"))
            continue
        stats["entries_checked"] += 1
        path = parsed["path"]
        digest = parsed["digest"]
        if excluded(path):
            stats["excluded"] += 1
            if len(excluded_rows) < max_print:
                excluded_rows.append({"path": path, "digest": digest})
            continue
        allowed = digests.get(path)
        if allowed is None:
            stats["path_missing"] += 1
            if len(path_missing) < max_print:
                path_missing.append({"path": path, "digest": digest, "line": parsed["line"]})
            continue
        if digest not in allowed:
            stats["hash_missing"] += 1
            if len(hash_missing) < max_print:
                hash_missing.append(
                    {
                        "path": path,
                        "digest": digest,
                        "allowed": sorted(allowed)[:10],
                        "line": parsed["line"],
                    }
                )
            continue
        stats["matched"] += 1

result = {
    "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    "policy": policy_path,
    "live_ima_file": live_path,
    "digests_paths": len(digests),
    "excludes": exclude_patterns,
    "stats": dict(stats),
    "path_missing": path_missing,
    "hash_missing": hash_missing,
    "malformed": malformed,
    "excluded_sample": excluded_rows,
}
json.dump(result, open(out_path, "w", encoding="utf-8"), indent=2, sort_keys=True)
open(out_path, "a", encoding="utf-8").write("\n")

print(f"out_json={out_path}")
print(f"digests_paths={len(digests)}")
for key in ("entries_checked", "matched", "excluded", "path_missing", "hash_missing", "malformed"):
    print(f"{key}={stats.get(key, 0)}")
print("---- path_missing ----")
for item in path_missing:
    print(f"{item['digest']} {item['path']}")
print("---- hash_missing ----")
for item in hash_missing:
    print(f"{item['digest']} {item['path']}")
PY
