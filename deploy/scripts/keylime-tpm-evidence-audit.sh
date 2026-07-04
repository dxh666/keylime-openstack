#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"

[ -r "$ENV_FILE" ] && source "$ENV_FILE"
[ -r "$OPENRC" ] && source "$OPENRC"

export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"

LOG_DIR="${KEYLIME_OPENSTACK_LOG_DIR:-/var/log}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BASE_DIR="${KEYLIME_TPM_EVIDENCE_DIR:-$LOG_DIR/keylime-openstack-tpm-evidence}"
RAW_DIR="$BASE_DIR/$TS"
OUT_JSON="${KEYLIME_TPM_EVIDENCE_BASELINE_JSON:-$LOG_DIR/keylime-openstack-tpm-evidence-baseline.json}"
MONITOR_URL="${KEYLIME_MONITOR_URL:-http://172.31.100.10:8088/api/status?force=1}"

mkdir -p "$RAW_DIR"

echo "audit_ts=$TS"
echo "raw_dir=$RAW_DIR"
echo "out_json=$OUT_JSON"

echo "=== Collect OpenStack state ==="
openstack hypervisor list --long -f json > "$RAW_DIR/openstack-hypervisors.json"
openstack compute service list -f json > "$RAW_DIR/openstack-compute-services.json"
openstack server list --all-projects --long -f json > "$RAW_DIR/openstack-servers.json" || true

curl -sS "$MONITOR_URL" > "$RAW_DIR/keylime-openstack-monitor.json" || true

[ -r /etc/keylime-openstack-sync/keylime-agent-inventory.env ] && \
  cp -a /etc/keylime-openstack-sync/keylime-agent-inventory.env "$RAW_DIR/keylime-agent-inventory.env" || true

[ -r /var/log/keylime-openstack-agent-inventory.json ] && \
  cp -a /var/log/keylime-openstack-agent-inventory.json "$RAW_DIR/keylime-openstack-agent-inventory.json" || true

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

echo "=== Collect TPM evidence from compute nodes ==="
while IFS=$'\t' read -r host ip state; do
  [ -n "$host" ] || continue

  raw="$RAW_DIR/tpm-${host}.txt"
  rc_file="$RAW_DIR/tpm-${host}.ssh_rc"

  echo "--- $host / $ip / state=$state ---"

  set +e
  ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=8 \
    -o StrictHostKeyChecking=no \
    "root@$ip" 'bash -s' > "$raw" 2>&1 <<'REMOTE'
set +e

echo "hostname=$(hostname)"
echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "kernel=$(uname -a)"

echo
echo "=== TPM device ==="
ls -l /dev/tpm* /dev/tpmrm* 2>/dev/null || echo "NO_TPM_DEVICE"

echo
echo "=== tpm2-tools ==="
command -v tpm2_pcrread || echo "NO_TPM2_PCRREAD"
command -v tpm2_getcap || echo "NO_TPM2_GETCAP"
command -v tpm2_eventlog || echo "NO_TPM2_EVENTLOG"

echo
echo "=== TPM fixed properties ==="
tpm2_getcap properties-fixed 2>&1 | egrep -i "FAMILY|LEVEL|REVISION|MANUFACTURER|VENDOR|FIRMWARE|PCR|HR_TRANSIENT" || true

echo
echo "=== TPM variable properties ==="
tpm2_getcap properties-variable 2>&1 | egrep -i "ownerAuthSet|endorsementAuthSet|lockoutAuthSet|disableClear|inLockout|LOCKOUT_COUNTER|MAX_AUTH_FAIL" || true

echo
echo "=== TPM PCR banks ==="
tpm2_getcap pcrs 2>&1 || true

echo
echo "=== PCR sha256 0-7 ==="
tpm2_pcrread sha256:0,1,2,3,4,5,6,7 2>&1 || true

echo
echo "=== PCR sha1 0-7 ==="
tpm2_pcrread sha1:0,1,2,3,4,5,6,7 2>&1 || true

echo
echo "=== Secure Boot state ==="
mokutil --sb-state 2>&1 || bootctl status 2>&1 | egrep -i "Secure Boot|Boot Loader" || echo "SECURE_BOOT_STATE_UNKNOWN"

echo
echo "=== Measured Boot event log ==="
if [ -r /sys/kernel/security/tpm0/binary_bios_measurements ]; then
  ls -l /sys/kernel/security/tpm0/binary_bios_measurements
  if command -v tpm2_eventlog >/dev/null 2>&1; then
    tpm2_eventlog /sys/kernel/security/tpm0/binary_bios_measurements 2>&1 | head -n 120
  fi
else
  echo "NO_TPM_EVENT_LOG"
fi

echo
echo "=== IMA runtime measurements ==="
ls -l /sys/kernel/security/ima/ascii_runtime_measurements 2>/dev/null || echo "NO_IMA_ASCII_LOG"
ls -l /sys/kernel/security/ima/binary_runtime_measurements 2>/dev/null || echo "NO_IMA_BINARY_LOG"

echo
echo "=== Keylime agent container ==="
docker ps --filter name=keylime-agent --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>&1 || true

echo
echo "=== Kernel trust-related logs ==="
dmesg 2>/dev/null | egrep -i "tpm|ima|secure|measured|integrity" | tail -n 80 || true
REMOTE
  ssh_rc=$?
  set -e

  echo "$ssh_rc" > "$rc_file"
  echo "ssh_rc=$ssh_rc raw=$raw"
done < "$RAW_DIR/openstack-compute-nodes.tsv"

echo "=== Build JSON audit file ==="
python3 - "$RAW_DIR" "$OUT_JSON" "$TS" <<'PY'
import json
import os
import re
import sys

raw_dir, out_json, ts = sys.argv[1], sys.argv[2], sys.argv[3]

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

nodes = []
tsv = os.path.join(raw_dir, "openstack-compute-nodes.tsv")
if os.path.exists(tsv):
    for line in open(tsv, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        host, ip, state = (line.split("\t") + ["", "", ""])[:3]
        raw_file = os.path.join(raw_dir, f"tpm-{host}.txt")
        rc_file = os.path.join(raw_dir, f"tpm-{host}.ssh_rc")
        raw = read_text(raw_file)
        ssh_rc = read_text(rc_file).strip()

        sha256 = {}
        in_sha256 = False
        for ln in raw.splitlines():
            if re.match(r"\s*sha256:\s*$", ln):
                in_sha256 = True
                continue
            if re.match(r"\s*sha1:\s*$", ln):
                in_sha256 = False
            m = re.match(r"\s*(\d+)\s*:\s*0x([0-9A-Fa-f]+)", ln)
            if in_sha256 and m:
                sha256[m.group(1)] = m.group(2).upper()

        nodes.append({
            "host": host,
            "ip": ip,
            "openstack_state": state,
            "ssh_rc": int(ssh_rc) if ssh_rc.isdigit() else None,
            "sha256_pcr_0_7": sha256,
            "has_tpm_device": "NO_TPM_DEVICE" not in raw,
            "has_event_log": "NO_TPM_EVENT_LOG" not in raw,
            "has_ima_ascii_log": "NO_IMA_ASCII_LOG" not in raw,
            "has_ima_binary_log": "NO_IMA_BINARY_LOG" not in raw,
            "secure_boot_unknown": "SECURE_BOOT_STATE_UNKNOWN" in raw,
            "keylime_agent_container_seen": "keylime-agent" in raw,
            "raw_file": raw_file,
        })

data = {
    "case": "Case 10 - TPM evidence baseline",
    "checked_at_utc": ts,
    "raw_dir": raw_dir,
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
        "sha256_pcr_count=", len(n["sha256_pcr_0_7"]),
        "event_log=", n["has_event_log"],
        "ima_ascii=", n["has_ima_ascii_log"],
        "ima_binary=", n["has_ima_binary_log"],
        "agent_container=", n["keylime_agent_container_seen"],
    )
PY

