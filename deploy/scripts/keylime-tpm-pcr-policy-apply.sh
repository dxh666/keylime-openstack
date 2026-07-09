#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"

[ -r "$ENV_FILE" ] && source "$ENV_FILE"
[ -r "$OPENRC" ] && source "$OPENRC"

TARGET="${1:-all}"
POLICY_ID="${2:-bound}"

KEYLIME_DIR="${KEYLIME_DIR:-/opt/keylime-docker}"
STATE_DIR="${KEYLIME_OPENSTACK_STATE_DIR:-/var/lib/keylime-openstack-sync}"
STORE_FILE="${KEYLIME_PCR_POLICY_FILE:-$STATE_DIR/tpm-pcr-policies.json}"
AUDIT_FILE="${KEYLIME_POLICY_APPLY_AUDIT_FILE:-/var/log/keylime-openstack-policy-apply.json}"
AGENT_PORT="${KEYLIME_AGENT_PORT:-9002}"
AGENT_API_VERSION="${KEYLIME_AGENT_API_VERSION:-2.5}"

VERIFIER_IP="${KEYLIME_VERIFIER_IP:-172.31.100.10}"
VERIFIER_PORT="${KEYLIME_VERIFIER_PORT:-8881}"
REGISTRAR_IP="${KEYLIME_REGISTRAR_IP:-172.31.100.10}"
REGISTRAR_PORT="${KEYLIME_REGISTRAR_PORT:-8891}"
RUN_SYNC="${KEYLIME_POLICY_APPLY_SYNC_NOW:-true}"
SYNC_MODE="${KEYLIME_POLICY_APPLY_SYNC_MODE:-control-loop}"

test -d "$KEYLIME_DIR"
test -r "$STORE_FILE"

parse_map_value() {
  local map="$1"
  local key="$2"
  echo "$map" | tr ',' '\n' | awk -F= -v k="$key" '$1 == k {print $2; exit}'
}

hosts=()
if [ "$TARGET" = "all" ]; then
  IFS=',' read -r -a hosts <<< "${KEYLIME_AGENT_HOSTS:-}"
else
  hosts=("$TARGET")
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
echo '{"applied":[],"failed":[]}' > "$tmp"

for host in "${hosts[@]}"; do
  host="${host//[[:space:]]/}"
  [ -z "$host" ] && continue

  uuid="$(parse_map_value "${KEYLIME_AGENT_UUID_MAP:-}" "$host")"
  ip="$(parse_map_value "${KEYLIME_AGENT_IP_MAP:-}" "$host")"

  selected_policy_id="$POLICY_ID"
  if [ "$POLICY_ID" = "bound" ]; then
    selected_policy_id="$(
      python3 - "$STORE_FILE" "$host" <<'PY'
import json
import sys
store, host = sys.argv[1], sys.argv[2]
d = json.load(open(store, encoding="utf-8"))
binding = d.get("bindings", {}).get(host, {})
if isinstance(binding, dict) and isinstance(binding.get("boot"), dict):
    print(binding["boot"].get("policy_id", ""))
elif isinstance(binding, dict):
    print(binding.get("policy_id", ""))
else:
    print("")
PY
    )"
  fi

  policy_json="$(
    python3 - "$STORE_FILE" "$selected_policy_id" <<'PY'
import json
import sys
store, policy_id = sys.argv[1], sys.argv[2]
d = json.load(open(store, encoding="utf-8"))
for p in d.get("policies", []):
    if p.get("id") == policy_id:
        policy = dict(p.get("tpm_policy", {}))
        policy.pop("mask", None)
        print(json.dumps(policy, separators=(",", ":"), sort_keys=True))
        break
PY
  )"

  if [ -z "$uuid" ] || [ -z "$ip" ] || [ -z "$selected_policy_id" ] || [ -z "$policy_json" ]; then
    echo "WARN: skip $host because uuid/ip/policy is missing"
    python3 - "$tmp" "$host" "$selected_policy_id" <<'PY'
import json
import sys
p, host, policy_id = sys.argv[1:]
d = json.load(open(p))
d["failed"].append({"host": host, "policy_id": policy_id, "reason": "missing_uuid_ip_or_policy"})
json.dump(d, open(p, "w"), indent=2)
PY
    continue
  fi

  echo "=== Apply TPM PCR policy to $host ==="
  echo "ip=$ip uuid=$uuid policy_id=$selected_policy_id"

  set +e
  (
    cd "$KEYLIME_DIR"
    docker compose run --rm keylime-tenant \
      -c update \
      -t "$ip" \
      -tp "$AGENT_PORT" \
      -u "$uuid" \
      -v "$VERIFIER_IP" \
      -vp "$VERIFIER_PORT" \
      -r "$REGISTRAR_IP" \
      -rp "$REGISTRAR_PORT" \
      --agent-api-version "$AGENT_API_VERSION" \
      --tpm_policy "$policy_json"
  )
  update_rc=$?

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
  set -e

  python3 - "$tmp" "$host" "$ip" "$uuid" "$selected_policy_id" "$update_rc" "$reactivate_rc" <<'PY'
import json
import sys
p, host, ip, uuid, policy_id, update_rc, reactivate_rc = sys.argv[1:]
d = json.load(open(p))
item = {
    "host": host,
    "ip": ip,
    "uuid": uuid,
    "policy_id": policy_id,
    "update_rc": int(update_rc),
    "reactivate_rc": int(reactivate_rc),
}
if int(update_rc) == 0 and int(reactivate_rc) == 0:
    d["applied"].append(item)
else:
    item["reason"] = "keylime_update_or_reactivate_failed"
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
json.dump(d, open(dst, "w"), indent=2)
open(dst, "a").write("\n")
PY

python3 -m json.tool "$AUDIT_FILE"

if [ "$RUN_SYNC" = "true" ]; then
  echo "=== Force trust sync after policy apply ==="
  if [ "$SYNC_MODE" = "control-loop" ]; then
    systemctl start keylime-openstack-sync.service || true
  else
    "${KEYLIME_OPENSTACK_SYNC_DIR:-/opt/keylime-openstack-sync}/keylime-placement-sync.sh" || true
  fi
fi

failed_count="$(python3 - "$AUDIT_FILE" <<'PY'
import json
import sys
d = json.load(open(sys.argv[1]))
print(len(d.get("failed", [])))
PY
)"

if [ "$failed_count" != "0" ]; then
  echo "ERROR: one or more policy apply actions failed"
  exit 1
fi
