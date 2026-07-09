#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"

[ -r "$ENV_FILE" ] && source "$ENV_FILE"
[ -r "$OPENRC" ] && source "$OPENRC"

export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"

LOG_DIR="${KEYLIME_OPENSTACK_LOG_DIR:-/var/log}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BASE_DIR="${KEYLIME_IMA_RUNTIME_EVIDENCE_DIR:-$LOG_DIR/keylime-openstack-ima-runtime-evidence}"
RAW_DIR="$BASE_DIR/$TS"
OUT_JSON="${KEYLIME_IMA_RUNTIME_BASELINE_JSON:-$LOG_DIR/keylime-openstack-ima-runtime-baseline.json}"
MONITOR_URL="${KEYLIME_MONITOR_URL:-http://172.31.100.10:8088/api/status?force=1}"
IMA_SAMPLE_LINES="${KEYLIME_IMA_SAMPLE_LINES:-200}"
RUNTIME_GUARD_PATH="${KEYLIME_RUNTIME_GUARD_PATH:-/opt/keylime-cloud-integrity/cloud-runtime-guard.sh}"

mkdir -p "$RAW_DIR"

echo "audit_ts=$TS"
echo "raw_dir=$RAW_DIR"
echo "out_json=$OUT_JSON"
echo "ima_sample_lines=$IMA_SAMPLE_LINES"
echo "runtime_guard_path=$RUNTIME_GUARD_PATH"

echo "=== Collect OpenStack state ==="
openstack hypervisor list --long -f json > "$RAW_DIR/openstack-hypervisors.json"
openstack compute service list -f json > "$RAW_DIR/openstack-compute-services.json"
openstack server list --all-projects --long -f json > "$RAW_DIR/openstack-servers.json" || true

curl -sS "$MONITOR_URL" > "$RAW_DIR/keylime-openstack-monitor.json" || true

python3 - "$RAW_DIR/openstack-hypervisors.json" "$RAW_DIR/openstack-compute-nodes.tsv" <<'PY'
import json
import sys

src, dst = sys.argv[1], sys.argv[2]
rows = json.load(open(src, encoding="utf-8"))

with open(dst, "w", encoding="utf-8") as f:
    for row in rows:
        host = row.get("Hypervisor Hostname") or row.get("hypervisor_hostname") or ""
        ip = row.get("Host IP") or row.get("host_ip") or ""
        state = row.get("State") or row.get("state") or ""
        if host and ip:
            f.write(f"{host}\t{ip}\t{state}\n")
PY

echo "=== Collect IMA / PCR10 runtime evidence from compute nodes ==="
while IFS=$'\t' read -r host ip state; do
  [ -n "$host" ] || continue

  raw="$RAW_DIR/ima-${host}.txt"
  rc_file="$RAW_DIR/ima-${host}.ssh_rc"

  echo "--- $host / $ip / state=$state ---"

  set +e
  ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=8 \
    -o StrictHostKeyChecking=no \
    "root@$ip" 'bash -s' -- "$IMA_SAMPLE_LINES" "$RUNTIME_GUARD_PATH" > "$raw" 2>&1 <<'REMOTE'
set +e

sample_lines="${1:-200}"
runtime_guard_path="${2:-/opt/keylime-cloud-integrity/cloud-runtime-guard.sh}"
ima_ascii="/sys/kernel/security/ima/ascii_runtime_measurements"
ima_binary="/sys/kernel/security/ima/binary_runtime_measurements"
ima_policy="/sys/kernel/security/ima/policy"

echo "hostname=$(hostname)"
echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "kernel=$(uname -a)"
echo "cmdline=$(cat /proc/cmdline 2>/dev/null)"

echo
echo "=== securityfs ==="
findmnt -T /sys/kernel/security 2>/dev/null || echo "SECURITYFS_NOT_MOUNTED"
ls -ld /sys/kernel/security /sys/kernel/security/ima 2>/dev/null || true

echo
echo "=== IMA files ==="
if [ -r "$ima_ascii" ]; then
  echo "IMA_ASCII_LOG_PRESENT"
  echo "ima_ascii_count=$(wc -l < "$ima_ascii" 2>/dev/null || echo 0)"
  ls -l "$ima_ascii"
else
  echo "NO_IMA_ASCII_LOG"
fi
if [ -r "$ima_binary" ]; then
  echo "IMA_BINARY_LOG_PRESENT"
  ls -l "$ima_binary"
else
  echo "NO_IMA_BINARY_LOG"
fi
if [ -r "$ima_policy" ]; then
  echo "IMA_POLICY_PRESENT"
  sed -n '1,120p' "$ima_policy" 2>/dev/null
else
  echo "NO_IMA_POLICY_READ"
fi

echo
echo "=== PCR sha256 10 ==="
tpm2_pcrread sha256:10 2>&1 || true

echo
echo "=== PCR sha1 10 ==="
tpm2_pcrread sha1:10 2>&1 || true

echo
echo "=== Runtime guard file ==="
if [ -e "$runtime_guard_path" ]; then
  echo "RUNTIME_GUARD_PRESENT"
  ls -l "$runtime_guard_path"
  printf 'runtime_guard_sha256='
  sha256sum "$runtime_guard_path" 2>/dev/null || true
else
  echo "NO_RUNTIME_GUARD"
fi

echo
echo "=== IMA runtime sample ==="
if [ -r "$ima_ascii" ]; then
  echo "--- first ${sample_lines} lines ---"
  head -n "$sample_lines" "$ima_ascii" 2>/dev/null || true
  echo "--- runtime guard matches ---"
  grep -F " $runtime_guard_path" "$ima_ascii" 2>/dev/null | tail -n 20 || true
fi

echo
echo "=== Kernel integrity logs ==="
dmesg 2>/dev/null | egrep -i "ima|integrity|audit|tpm|secure" | tail -n 120 || true
REMOTE
  ssh_rc=$?
  set -e

  echo "$ssh_rc" > "$rc_file"
  echo "ssh_rc=$ssh_rc raw=$raw"
done < "$RAW_DIR/openstack-compute-nodes.tsv"

echo "=== Build JSON audit file ==="
python3 - "$RAW_DIR" "$OUT_JSON" "$TS" "$RUNTIME_GUARD_PATH" <<'PY'
import json
import os
import re
import sys

raw_dir, out_json, ts, runtime_guard_path = sys.argv[1:5]

def read_text(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        return ""

def read_json(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None

def parse_pcr(raw, alg, pcr):
    active_alg = ""
    for line in raw.splitlines():
        if re.match(r"\s*sha256:\s*$", line):
            active_alg = "sha256"
            continue
        if re.match(r"\s*sha1:\s*$", line):
            active_alg = "sha1"
            continue
        match = re.match(r"\s*(\d+)\s*:\s*0x([0-9A-Fa-f]+)", line)
        if active_alg == alg and match and match.group(1) == str(pcr):
            return match.group(2).upper()
    return ""

nodes = []
tsv = os.path.join(raw_dir, "openstack-compute-nodes.tsv")
if os.path.exists(tsv):
    for line in open(tsv, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        host, ip, state = (line.split("\t") + ["", "", ""])[:3]
        raw_file = os.path.join(raw_dir, f"ima-{host}.txt")
        rc_file = os.path.join(raw_dir, f"ima-{host}.ssh_rc")
        raw = read_text(raw_file)
        ssh_rc = read_text(rc_file).strip()
        count_match = re.search(r"ima_ascii_count=(\d+)", raw)
        guard_match = re.search(r"runtime_guard_sha256=([0-9A-Fa-f]{64})\s+", raw)

        nodes.append({
            "host": host,
            "ip": ip,
            "openstack_state": state,
            "ssh_rc": int(ssh_rc) if ssh_rc.isdigit() else None,
            "has_ima_ascii_log": "IMA_ASCII_LOG_PRESENT" in raw,
            "has_ima_binary_log": "IMA_BINARY_LOG_PRESENT" in raw,
            "has_ima_policy_read": "IMA_POLICY_PRESENT" in raw,
            "ima_ascii_count": int(count_match.group(1)) if count_match else None,
            "pcr10_sha256": parse_pcr(raw, "sha256", 10),
            "pcr10_sha1": parse_pcr(raw, "sha1", 10),
            "securityfs_mounted": "SECURITYFS_NOT_MOUNTED" not in raw,
            "runtime_guard_path": runtime_guard_path,
            "runtime_guard_exists": "RUNTIME_GUARD_PRESENT" in raw,
            "runtime_guard_sha256": guard_match.group(1).upper() if guard_match else "",
            "raw_file": raw_file,
        })

data = {
    "case": "Case 11 - IMA runtime integrity evidence baseline",
    "checked_at_utc": ts,
    "raw_dir": raw_dir,
    "runtime_guard_path": runtime_guard_path,
    "openstack": {
        "hypervisors": read_json(os.path.join(raw_dir, "openstack-hypervisors.json")),
        "compute_services": read_json(os.path.join(raw_dir, "openstack-compute-services.json")),
    },
    "keylime_openstack_monitor": read_json(os.path.join(raw_dir, "keylime-openstack-monitor.json")),
    "nodes": nodes,
}

with open(out_json, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY

echo
echo "=== Summary ==="
python3 - "$OUT_JSON" <<'PY'
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
print("case:", d["case"])
print("checked_at_utc:", d["checked_at_utc"])
print("raw_dir:", d["raw_dir"])
print("audit_file:", sys.argv[1])
for n in d["nodes"]:
    print(
        n["host"],
        "ip=", n["ip"],
        "ssh_rc=", n["ssh_rc"],
        "ima_ascii=", n["has_ima_ascii_log"],
        "ima_count=", n["ima_ascii_count"],
        "pcr10_sha256=", bool(n["pcr10_sha256"]),
        "guard=", n["runtime_guard_exists"],
    )
PY
