#!/usr/bin/env bash

load_keylime_agent_inventory() {
  local candidate
  local candidates=()

  if [ -n "${KEYLIME_AGENT_INVENTORY_FILE:-}" ]; then
    candidates+=("$KEYLIME_AGENT_INVENTORY_FILE")
  fi
  candidates+=(
    "/etc/keylime-openstack/keylime-agent-inventory.env"
    "/etc/keylime-openstack-sync/keylime-agent-inventory.env"
  )

  for candidate in "${candidates[@]}"; do
    if [ -r "$candidate" ]; then
      # shellcheck disable=SC1090
      source "$candidate"
      return 0
    fi
  done
  return 0
}

keylime_map_value() {
  local map="$1"
  local key="$2"
  echo "$map" | tr ',' '\n' | awk -F= -v k="$key" '$1 == k {print $2; exit}'
}

keylime_api_node_value() {
  local host="$1"
  local field="$2"
  local api_url="${KEYLIME_OPENSTACK_API_URL:-http://127.0.0.1:8088}"

  python3 - "$api_url" "$host" "$field" <<'PY'
import json
import sys
import urllib.error
import urllib.request

api_url, host, field = sys.argv[1:4]
url = api_url.rstrip("/") + "/api/nodes"

try:
    with urllib.request.urlopen(url, timeout=5) as response:
        nodes = json.load(response)
except (OSError, urllib.error.URLError, json.JSONDecodeError):
    print("")
    raise SystemExit(0)

for node in nodes:
    if not isinstance(node, dict):
        continue
    names = {
        str(node.get("hostname") or ""),
        str(node.get("hypervisor_name") or ""),
    }
    if host not in names:
        continue
    if field == "ip":
        print(node.get("keylime_agent_ip") or node.get("management_ip") or "")
    elif field == "uuid":
        print(node.get("keylime_agent_uuid") or "")
    elif field == "host":
        print(node.get("hostname") or node.get("hypervisor_name") or "")
    break
PY
}

keylime_resolve_agent_ip() {
  local host="$1"
  local value="${KEYLIME_RUNTIME_POLICY_HOST_IP:-}"
  if [ -z "$value" ]; then
    value="$(keylime_map_value "${KEYLIME_AGENT_IP_MAP:-}" "$host")"
  fi
  if [ -z "$value" ]; then
    value="$(keylime_api_node_value "$host" ip)"
  fi
  printf '%s\n' "$value"
}

keylime_resolve_agent_uuid() {
  local host="$1"
  local value
  value="$(keylime_map_value "${KEYLIME_AGENT_UUID_MAP:-}" "$host")"
  if [ -z "$value" ]; then
    value="$(keylime_api_node_value "$host" uuid)"
  fi
  printf '%s\n' "$value"
}

keylime_resolve_agent_hosts() {
  local target="$1"
  if [ "$target" != "all" ]; then
    printf '%s\n' "$target"
    return 0
  fi
  if [ -n "${KEYLIME_AGENT_HOSTS:-}" ]; then
    printf '%s\n' "$KEYLIME_AGENT_HOSTS"
    return 0
  fi

  local api_url="${KEYLIME_OPENSTACK_API_URL:-http://127.0.0.1:8088}"
  python3 - "$api_url" <<'PY'
import json
import sys
import urllib.error
import urllib.request

api_url = sys.argv[1]
url = api_url.rstrip("/") + "/api/nodes"

try:
    with urllib.request.urlopen(url, timeout=5) as response:
        nodes = json.load(response)
except (OSError, urllib.error.URLError, json.JSONDecodeError):
    print("")
    raise SystemExit(0)

hosts = []
for node in nodes:
    if not isinstance(node, dict):
        continue
    if node.get("role") != "compute" or node.get("enabled") is False:
        continue
    host = node.get("hostname") or node.get("hypervisor_name")
    if host:
        hosts.append(str(host))
print(",".join(hosts))
PY
}
