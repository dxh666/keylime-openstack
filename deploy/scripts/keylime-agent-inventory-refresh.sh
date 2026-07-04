#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${KEYLIME_OPENSTACK_ENV_FILE:-/etc/keylime-openstack-sync/openstack-keylime-lab.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

OPENRC="${OPENRC:-/etc/kolla/admin-openrc.sh}"
KEYLIME_DIR="${KEYLIME_DIR:-/opt/keylime-docker}"
LOG_DIR="${KEYLIME_OPENSTACK_LOG_DIR:-/var/log}"
COMPUTE_SERVICE="${COMPUTE_SERVICE:-nova-compute}"
REGISTRAR_IP="${REGISTRAR_IP:-${KEYLIME_REGISTRAR_IP:-172.31.100.10}}"
REGISTRAR_PORT="${REGISTRAR_PORT:-${KEYLIME_REGISTRAR_PORT:-8891}}"
INVENTORY_FILE="${KEYLIME_AGENT_INVENTORY_FILE:-/etc/keylime-openstack-sync/keylime-agent-inventory.env}"
INVENTORY_JSON="${KEYLIME_AGENT_INVENTORY_JSON:-$LOG_DIR/keylime-openstack-agent-inventory.json}"
REFRESH_SECONDS="${KEYLIME_AGENT_INVENTORY_REFRESH_SECONDS:-60}"
ALLOW_EMPTY="${KEYLIME_AGENT_INVENTORY_ALLOW_EMPTY:-false}"
FORCE="${KEYLIME_AGENT_INVENTORY_FORCE:-false}"

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

if [ ! -d "$KEYLIME_DIR" ]; then
  echo "ERROR: Keylime docker directory not found: $KEYLIME_DIR"
  exit 1
fi

if [ -s "$INVENTORY_FILE" ] && ! is_truthy "$FORCE"; then
  now="$(date +%s)"
  mtime="$(stat -c %Y "$INVENTORY_FILE" 2>/dev/null || echo 0)"
  age=$((now - mtime))
  if [ "$age" -lt "$REFRESH_SECONDS" ]; then
    echo "Keylime agent inventory is fresh enough: $INVENTORY_FILE age=${age}s"
    exit 0
  fi
fi

install -d -m 0755 "$(dirname "$INVENTORY_FILE")" "$(dirname "$INVENTORY_JSON")"

tmp_dir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

services_json="$tmp_dir/openstack-compute-services.json"
hypervisors_json="$tmp_dir/openstack-hypervisors.json"
reglist_raw="$tmp_dir/keylime-reglist.raw"
inventory_tmp="$tmp_dir/keylime-agent-inventory.env"
inventory_json_tmp="$tmp_dir/keylime-openstack-agent-inventory.json"

source "$OPENRC"

openstack compute service list -f json > "$services_json"
openstack hypervisor list --long -f json > "$hypervisors_json" || \
  openstack hypervisor list -f json > "$hypervisors_json"

set +e
(
  cd "$KEYLIME_DIR" && \
  docker compose run --rm keylime-tenant \
    -c reglist \
    -r "$REGISTRAR_IP" \
    -rp "$REGISTRAR_PORT"
) > "$reglist_raw" 2>&1
reglist_rc=$?
set -e

if [ "$reglist_rc" -ne 0 ]; then
  echo "ERROR: keylime-tenant reglist failed with rc=$reglist_rc"
  cat "$reglist_raw"
  exit "$reglist_rc"
fi

python3 - \
  "$services_json" \
  "$hypervisors_json" \
  "$reglist_raw" \
  "$inventory_tmp" \
  "$inventory_json_tmp" \
  "$COMPUTE_SERVICE" \
  "$ALLOW_EMPTY" \
  "${KEYLIME_AGENT_HOSTS:-}" \
  "${KEYLIME_AGENT_IP_MAP:-}" \
  "${KEYLIME_AGENT_UUID_MAP:-}" <<'PY'
import ast
import datetime as dt
import ipaddress
import json
import re
import sys
from pathlib import Path

(
    services_path,
    hypervisors_path,
    reglist_path,
    env_path,
    json_path,
    compute_service,
    allow_empty,
    static_hosts_csv,
    static_ip_map_csv,
    static_uuid_map_csv,
) = sys.argv[1:11]
allow_empty = allow_empty.strip().lower() in {"1", "true", "yes", "y", "on"}

UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def load_json(path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        value = f.read().strip()
    if not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else [parsed]


def norm_key(key):
    return str(key).strip().lower().replace(" ", "_").replace("-", "_")


def pick(row, names, default=""):
    normalized = {norm_key(key): val for key, val in row.items()}
    for name in names:
        key = norm_key(name)
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return default


def valid_ip(value):
    try:
        parsed = ipaddress.ip_address(str(value).strip())
    except ValueError:
        return ""
    if parsed.version != 4:
        return ""
    return str(parsed)


def quote_env(value):
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def split_csv(value):
    return [item.strip() for item in str(value).split(",") if item.strip()]


def parse_mapping(value):
    mapping = {}
    for item in split_csv(value):
        if "=" not in item:
            continue
        key, val = item.split("=", 1)
        key = key.strip()
        val = val.strip()
        if key and val:
            mapping[key] = val
    return mapping


def same_host(left, right):
    return left == right or left.split(".", 1)[0] == right.split(".", 1)[0]


services = load_json(services_path)
hypervisors = load_json(hypervisors_path)

compute_hosts = []
for row in services:
    if str(pick(row, ["binary"], "")).strip() != compute_service:
        continue
    host = str(pick(row, ["host"], "")).strip()
    if host and host not in compute_hosts:
        compute_hosts.append(host)

hypervisor_by_host = {}
for row in hypervisors:
    host = str(pick(row, ["hypervisor_hostname", "hypervisor hostname", "hostname", "name"], "")).strip()
    if not host:
        continue
    ip = valid_ip(pick(row, ["host_ip", "host ip"], ""))
    hypervisor_by_host[host] = {"host": host, "ip": ip, "raw": row}
    short = host.split(".", 1)[0]
    hypervisor_by_host.setdefault(short, hypervisor_by_host[host])
    if host not in compute_hosts:
        compute_hosts.append(host)

reglist_raw = Path(reglist_path).read_text(encoding="utf-8-sig", errors="replace")


def parse_structured(raw):
    text = raw.strip()
    candidates = [text]
    json_start = min([i for i in [text.find("{"), text.find("[")] if i >= 0], default=-1)
    if json_start >= 0:
        candidates.append(text[json_start:])

    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        for loader in (json.loads, ast.literal_eval):
            try:
                parsed = loader(candidate)
            except Exception:
                continue
            records = []
            collect_records(parsed, records)
            if records:
                return records
    return []


def collect_records(value, records, inherited_uuid=""):
    if isinstance(value, dict):
        uuid = inherited_uuid
        for key, val in value.items():
            key_text = str(key)
            if UUID_RE.fullmatch(key_text.strip()):
                if isinstance(val, dict):
                    collect_records(val, records, key_text.strip())
                else:
                    records.append({"uuid": key_text.strip(), "ip": ""})

        normalized = {norm_key(key): val for key, val in value.items()}
        uuid = str(
            normalized.get("uuid")
            or normalized.get("agent_uuid")
            or normalized.get("agent_id")
            or normalized.get("agentid")
            or inherited_uuid
            or ""
        ).strip()
        ip = ""
        for key in ("contact_ip", "agent_ip", "ip", "host_ip", "address"):
            if key in normalized:
                ip = valid_ip(normalized[key])
                if ip:
                    break
        if UUID_RE.fullmatch(uuid) and ip:
            records.append({"uuid": uuid, "ip": ip})

        for val in value.values():
            collect_records(val, records, uuid if UUID_RE.fullmatch(uuid) else inherited_uuid)
    elif isinstance(value, list):
        for item in value:
            collect_records(item, records, inherited_uuid)


def parse_text(raw):
    records = []
    current_uuid = ""
    for line in raw.splitlines():
        uuids = UUID_RE.findall(line)
        ips = [ip for ip in IP_RE.findall(line) if valid_ip(ip)]
        if uuids:
            current_uuid = uuids[-1]
        if uuids and ips:
            records.append({"uuid": uuids[-1], "ip": ips[-1]})
            continue
        if current_uuid and ips and re.search(r"\b(contact[_ -]?ip|agent[_ -]?ip|ip|address|host_ip)\b", line, re.I):
            records.append({"uuid": current_uuid, "ip": ips[-1]})
    return records


agent_records = parse_structured(reglist_raw) + parse_text(reglist_raw)
agent_by_ip = {}
for record in agent_records:
    uuid = str(record.get("uuid", "")).strip()
    ip = valid_ip(record.get("ip", ""))
    if UUID_RE.fullmatch(uuid) and ip:
        agent_by_ip[ip] = {"uuid": uuid, "ip": ip}

static_hosts = split_csv(static_hosts_csv)
static_ip_map = parse_mapping(static_ip_map_csv)
static_uuid_map = parse_mapping(static_uuid_map_csv)
for host in [*static_ip_map.keys(), *static_uuid_map.keys()]:
    if host and host not in static_hosts:
        static_hosts.append(host)

matched = []
unmatched_hosts = []
for host in compute_hosts:
    hypervisor = hypervisor_by_host.get(host) or hypervisor_by_host.get(host.split(".", 1)[0], {})
    ip = valid_ip(hypervisor.get("ip", ""))
    agent = agent_by_ip.get(ip) if ip else None
    if agent:
        matched.append({"host": host, "ip": ip, "uuid": agent["uuid"], "source": "auto_reglist_ip"})
    else:
        unmatched_hosts.append({"host": host, "ip": ip})

matched_hosts = {item["host"] for item in matched}
static_fallback = []
for host in static_hosts:
    if any(same_host(host, matched_host) for matched_host in matched_hosts):
        continue
    if compute_hosts and not any(same_host(host, compute_host) for compute_host in compute_hosts):
        continue
    uuid = static_uuid_map.get(host, "").strip()
    if not UUID_RE.fullmatch(uuid):
        continue
    hypervisor = hypervisor_by_host.get(host) or hypervisor_by_host.get(host.split(".", 1)[0], {})
    ip = valid_ip(static_ip_map.get(host, "")) or valid_ip(hypervisor.get("ip", ""))
    if not ip:
        continue
    static_fallback.append({"host": host, "ip": ip, "uuid": uuid, "source": "static_env_fallback"})

if static_fallback:
    matched.extend(static_fallback)
    fallback_hosts = {item["host"] for item in static_fallback}
    unmatched_hosts = [
        item for item in unmatched_hosts
        if not any(same_host(item["host"], fallback_host) for fallback_host in fallback_hosts)
    ]

if not matched and not allow_empty:
    raise SystemExit(
        "no OpenStack compute host matched a Keylime registered agent and no static fallback was usable; "
        "inventory not updated"
    )

hosts = ",".join(item["host"] for item in matched)
ip_map = ",".join(f'{item["host"]}={item["ip"]}' for item in matched)
uuid_map = ",".join(f'{item["host"]}={item["uuid"]}' for item in matched)
generated_at = dt.datetime.now(dt.timezone.utc).isoformat()

env_lines = [
    "# Generated by keylime-agent-inventory-refresh.sh; do not edit by hand.",
    f"export KEYLIME_AGENT_INVENTORY_GENERATED_AT={quote_env(generated_at)}",
    f"export KEYLIME_AGENT_HOSTS={quote_env(hosts)}",
    f"export KEYLIME_AGENT_IP_MAP={quote_env(ip_map)}",
    f"export KEYLIME_AGENT_UUID_MAP={quote_env(uuid_map)}",
    "",
]
Path(env_path).write_text("\n".join(env_lines), encoding="utf-8")

payload = {
    "generated_at_utc": generated_at,
    "matched": matched,
    "unmatched_openstack_compute_hosts": unmatched_hosts,
    "registered_agents": sorted(agent_by_ip.values(), key=lambda item: item["ip"]),
    "static_fallback_hosts": static_fallback,
    "reglist_record_count": len(agent_records),
}
Path(json_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print(f"matched_agents={len(matched)}")
for item in matched:
    print(f"{item['host']} ip={item['ip']} uuid={item['uuid']}")
if unmatched_hosts:
    print("unmatched_openstack_compute_hosts=" + ",".join(item["host"] for item in unmatched_hosts))
PY

install -m 0644 "$inventory_tmp" "$INVENTORY_FILE"
install -m 0644 "$inventory_json_tmp" "$INVENTORY_JSON"

echo "inventory_file=$INVENTORY_FILE"
echo "inventory_json=$INVENTORY_JSON"
