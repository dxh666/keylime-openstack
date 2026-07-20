#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  keylime-only-attestation-repair.sh [--hosts host1,host2|all]

Examples:
  keylime-only-attestation-repair.sh
  keylime-only-attestation-repair.sh --hosts csri8,csri9
  keylime-only-attestation-repair.sh --hosts hygon22 --check-count 5 --check-interval 30

Repair Keylime-only IMA runtime drift without enabling OpenStack enforcement.
The helper:

  1. runs the Keylime-only gate and reads failed nodes
  2. selects nodes whose boot evidence passes but IMA runtime policy fails
  3. runs a live-vs-bound IMA policy diff for each selected node
  4. refreshes and force-replaces the node's Keylime runtime policy
  5. runs a final stability gate

It intentionally does not refresh boot/PCR policy. If boot evidence fails, audit
TPM/PCR evidence first instead of learning a new IMA runtime baseline.

Options:
  --hosts <hosts>          Comma-separated host filter. Defaults to all nodes.
  --check-count <count>    Final stability gate count. Default: 3.
  --check-interval <sec>   Final stability gate interval. Default: 30.
  --sync-delay <sec>       Wait after each Keylime policy apply. Default: 120.
  --skip-diff              Refresh without printing live policy diff first.
  --dry-run                Print selected repair commands without running them.
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_SCRIPT="$SCRIPT_DIR/keylime-only-attestation-check.sh"
DIFF_SCRIPT="$SCRIPT_DIR/keylime-ima-runtime-policy-diff.sh"
REFRESH_SCRIPT="$SCRIPT_DIR/keylime-ima-runtime-policy-refresh.sh"

HOSTS=""
CHECK_COUNT="${KEYLIME_REPAIR_CHECK_COUNT:-3}"
CHECK_INTERVAL="${KEYLIME_REPAIR_CHECK_INTERVAL_SECONDS:-30}"
SYNC_DELAY="${KEYLIME_RUNTIME_POLICY_APPLY_SYNC_DELAY_SECONDS:-120}"
SKIP_DIFF=false
DRY_RUN=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --hosts)
      HOSTS="${2:-}"
      shift 2
      ;;
    --check-count)
      CHECK_COUNT="${2:-}"
      shift 2
      ;;
    --check-interval)
      CHECK_INTERVAL="${2:-}"
      shift 2
      ;;
    --sync-delay)
      SYNC_DELAY="${2:-}"
      shift 2
      ;;
    --skip-diff)
      SKIP_DIFF=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$CHECK_COUNT" in
  ''|*[!0-9]*)
    echo "ERROR: --check-count must be a non-negative integer" >&2
    exit 1
    ;;
esac
case "$CHECK_INTERVAL" in
  ''|*[!0-9]*)
    echo "ERROR: --check-interval must be a non-negative integer" >&2
    exit 1
    ;;
esac
case "$SYNC_DELAY" in
  ''|*[!0-9]*)
    echo "ERROR: --sync-delay must be a non-negative integer" >&2
    exit 1
    ;;
esac

check_args=(--failures-only)
final_args=(--strict --count "$CHECK_COUNT" --interval "$CHECK_INTERVAL")
if [ -n "$HOSTS" ] && [ "$HOSTS" != "all" ]; then
  check_args+=(--hosts "$HOSTS")
  final_args+=(--hosts "$HOSTS")
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
initial_json="$tmp_dir/initial-check.json"
repair_hosts_file="$tmp_dir/repair-hosts.txt"

echo "=== Keylime-only pre-check ==="
bash "$CHECK_SCRIPT" "${check_args[@]}" | tee "$initial_json"

python3 - "$initial_json" > "$repair_hosts_file" <<'PY'
import json
import sys

raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
start = raw.find("{")
end = raw.rfind("}")
if start < 0 or end < start:
    raise SystemExit("ERROR: cannot find JSON object in pre-check output")

data = json.loads(raw[start : end + 1])
hosts = []
for node in data.get("nodes", []):
    if not isinstance(node, dict):
        continue
    host = str(node.get("host") or "").strip()
    evidence = node.get("evidence") if isinstance(node.get("evidence"), dict) else {}
    remediation = (
        node.get("remediation") if isinstance(node.get("remediation"), dict) else {}
    )
    event_id = str(node.get("last_event_id") or "").lower()
    boot_status = str(evidence.get("boot") or "").lower()
    runtime_status = str(evidence.get("runtime") or "").lower()
    category = str(remediation.get("category") or "").lower()

    runtime_drift = (
        category == "ima-runtime-policy"
        or event_id.startswith("ima.validation.")
        or runtime_status == "fail"
    )
    if host and boot_status == "pass" and runtime_drift:
        hosts.append(host)

print("\n".join(dict.fromkeys(hosts)))
PY

mapfile -t repair_hosts < "$repair_hosts_file"

if [ "${#repair_hosts[@]}" -eq 0 ]; then
  echo "repair_hosts=[]"
else
  printf 'repair_hosts=[%s]\n' "$(IFS=,; echo "${repair_hosts[*]}")"
fi

repair_failed=0
for host in "${repair_hosts[@]}"; do
  [ -n "$host" ] || continue
  echo "=== Repair IMA runtime policy for $host ==="

  if [ "$SKIP_DIFF" != "true" ]; then
    if [ "$DRY_RUN" = "true" ]; then
      echo "DRY_RUN: bash $DIFF_SCRIPT $host bound"
    else
      if ! bash "$DIFF_SCRIPT" "$host" bound; then
        echo "WARN: diff failed for $host; continuing with refresh" >&2
        repair_failed=1
      fi
    fi
  fi

  if [ "$DRY_RUN" = "true" ]; then
    echo "DRY_RUN: KEYLIME_RUNTIME_POLICY_INCLUDE_BOUND_BOOT=true KEYLIME_RUNTIME_POLICY_APPLY_FORCE_REPLACE=true KEYLIME_RUNTIME_POLICY_APPLY_SYNC_DELAY_SECONDS=$SYNC_DELAY bash $REFRESH_SCRIPT $host"
    continue
  fi

  if ! KEYLIME_RUNTIME_POLICY_INCLUDE_BOUND_BOOT=true \
    KEYLIME_RUNTIME_POLICY_APPLY_FORCE_REPLACE=true \
    KEYLIME_RUNTIME_POLICY_APPLY_SYNC_DELAY_SECONDS="$SYNC_DELAY" \
      bash "$REFRESH_SCRIPT" "$host"; then
    echo "ERROR: repair refresh failed for $host" >&2
    repair_failed=1
  fi
done

if [ "$DRY_RUN" = "true" ]; then
  exit "$repair_failed"
fi

echo "=== Keylime-only final stability gate ==="
set +e
bash "$CHECK_SCRIPT" "${final_args[@]}"
final_rc=$?
set -e

if [ "$repair_failed" -ne 0 ]; then
  exit "$repair_failed"
fi
exit "$final_rc"
