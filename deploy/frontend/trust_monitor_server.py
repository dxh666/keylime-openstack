#!/usr/bin/env python3
"""Read-only status API for the Keylime + OpenStack trust monitor."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import subprocess
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


APP_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = "/etc/keylime-openstack-sync/openstack-keylime-lab.env"
DEFAULTS = {
    "OPENRC": "/etc/kolla/admin-openrc.sh",
    "KEYLIME_OPENSTACK_LOG_DIR": "/var/log",
    "KEYLIME_OPENSTACK_STATE_DIR": "/var/lib/keylime-openstack-sync",
    "RP_NAME": "csri9",
    "TRUSTED_TRAIT": "CUSTOM_KEYLIME_ATTESTED",
    "COMPUTE_HOST": "csri9",
    "COMPUTE_SERVICE": "nova-compute",
    "KEYLIME_AGENT_IP": "172.31.100.9",
    "KEYLIME_AGENT_HOSTS": "",
    "KEYLIME_AGENT_IP_MAP": "",
}


def load_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.is_file():
        return env

    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$")
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        key, raw_value = match.groups()
        try:
            parts = shlex.split(raw_value, comments=False, posix=True)
            value = parts[0] if parts else ""
        except ValueError:
            value = raw_value.strip().strip("\"'")
        env[key] = value
    return env


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_mapping(value: str) -> dict[str, str]:
    if not value.strip():
        return {}
    if value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
            return {str(key): str(val) for key, val in parsed.items()}
        except json.JSONDecodeError:
            return {}

    mapping: dict[str, str] = {}
    for item in split_csv(value):
        if "=" not in item:
            continue
        key, val = item.split("=", 1)
        mapping[key.strip()] = val.strip()
    return mapping


def build_config(env_file: str) -> dict[str, Any]:
    config: dict[str, Any] = dict(DEFAULTS)
    config.update(load_env_file(env_file))
    for key in DEFAULTS:
        if os.environ.get(key):
            config[key] = os.environ[key]

    agent_hosts = split_csv(str(config.get("KEYLIME_AGENT_HOSTS", "")))
    if not agent_hosts and config.get("COMPUTE_HOST"):
        agent_hosts = [str(config["COMPUTE_HOST"])]

    agent_ip_map = parse_mapping(str(config.get("KEYLIME_AGENT_IP_MAP", "")))
    if config.get("COMPUTE_HOST") and config.get("KEYLIME_AGENT_IP"):
        agent_ip_map.setdefault(str(config["COMPUTE_HOST"]), str(config["KEYLIME_AGENT_IP"]))

    config["ENV_FILE"] = env_file
    config["AGENT_HOSTS"] = agent_hosts
    config["AGENT_IP_BY_HOST"] = agent_ip_map
    return config


def normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace(" ", "_").replace("-", "_")


def pick(row: dict[str, Any], names: list[str], default: Any = "") -> Any:
    normalized = {normalize_key(key): val for key, val in row.items()}
    for name in names:
        key = normalize_key(name)
        if key in normalized and normalized[key] not in (None, ""):
            return normalized[key]
    return default


def parse_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+", text):
        return int(text)
    return None


def pick_running_vms(row: dict[str, Any]) -> int | None:
    value = pick(
        row,
        [
            "running_vms",
            "running vms",
            "running_vms_",
            "running instances",
            "running_instances",
            "instances",
            "servers",
            "vms",
        ],
        "",
    )
    return parse_count(value)


def run_command(args: list[str], timeout: int = 8) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "rc": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    except FileNotFoundError as exc:
        return {"rc": 127, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "stdout": "", "stderr": f"timeout after {timeout}s"}


def run_openstack(config: dict[str, Any], script: str, timeout: int = 18) -> dict[str, Any]:
    openrc = Path(str(config["OPENRC"]))
    if not openrc.is_file():
        return {
            "rc": 1,
            "stdout": "",
            "stderr": f"OpenStack RC file not readable: {openrc}",
        }

    wrapped = "\n".join(
        [
            "set -o pipefail",
            f"source {shlex.quote(str(openrc))}",
            'export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"',
            script,
        ]
    )
    return run_command(["/usr/bin/env", "bash", "-lc", wrapped], timeout=timeout)


def run_openstack_json(config: dict[str, Any], script: str, timeout: int = 18) -> tuple[list[dict[str, Any]], list[str]]:
    result = run_openstack(config, script, timeout=timeout)
    if result["rc"] != 0:
        return [], [result["stderr"] or f"{script} rc={result['rc']}"]
    try:
        parsed = json.loads(result["stdout"] or "[]")
    except json.JSONDecodeError as exc:
        return [], [f"OpenStack JSON invalid for '{script}': {exc}"]
    if isinstance(parsed, dict):
        return [parsed], []
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)], []
    return [], [f"OpenStack JSON has unsupported type for '{script}'"]


def read_decision(config: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(config["KEYLIME_OPENSTACK_LOG_DIR"])) / "keylime-openstack-sync-decision.json"
    if not path.is_file():
        return {"path": str(path), "result": "UNKNOWN", "reason": "DECISION_FILE_NOT_FOUND"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"path": str(path), "result": "UNKNOWN", "reason": f"DECISION_JSON_INVALID: {exc}"}
    data["path"] = str(path)
    return data


def read_timer() -> dict[str, Any]:
    active = run_command(["systemctl", "is-active", "keylime-openstack-sync.timer"])
    enabled = run_command(["systemctl", "is-enabled", "keylime-openstack-sync.timer"])
    timers = run_command(["systemctl", "list-timers", "--all", "--no-pager"])
    timer_lines = [
        line for line in timers["stdout"].splitlines() if "keylime-openstack-sync" in line
    ]
    errors = []
    for item in (active, enabled, timers):
        if item["rc"] not in (0, 1, 3):
            errors.append(item["stderr"] or f"systemctl rc={item['rc']}")
    return {
        "active": active["stdout"] or "unknown",
        "enabled": enabled["stdout"] or "unknown",
        "list_timers": "\n".join(timer_lines),
        "errors": errors,
    }


def normalize_compute_service(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(pick(row, ["id"], "")),
        "binary": str(pick(row, ["binary", "service"], "")),
        "host": str(pick(row, ["host"], "")),
        "zone": str(pick(row, ["zone", "availability_zone"], "")),
        "status": str(pick(row, ["status"], "")).lower(),
        "state": str(pick(row, ["state"], "")).lower(),
        "updated_at": str(pick(row, ["updated_at", "updated at"], "")),
        "raw": row,
    }


def read_compute_services(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows, errors = run_openstack_json(config, "openstack compute service list -f json")
    service_name = str(config["COMPUTE_SERVICE"])
    services = [
        normalize_compute_service(row)
        for row in rows
        if str(pick(row, ["binary", "service"], "")) == service_name
    ]
    services.sort(key=lambda item: item["host"])
    return services, errors


def read_hypervisors(config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows, errors = run_openstack_json(config, "openstack hypervisor list --long -f json")
    if errors:
        rows, errors = run_openstack_json(config, "openstack hypervisor list -f json")

    hypervisors: dict[str, dict[str, Any]] = {}
    for row in rows:
        hostname = str(
            pick(
                row,
                ["hypervisor_hostname", "hypervisor hostname", "hostname", "name"],
                "",
            )
        )
        if not hostname:
            continue
        hypervisors[hostname] = {
            "id": str(pick(row, ["id"], "")),
            "hostname": hostname,
            "host_ip": str(pick(row, ["host_ip", "host ip"], "")),
            "running_vms": pick_running_vms(row),
            "vm_count_source": "hypervisor list" if pick_running_vms(row) is not None else "",
            "state": str(pick(row, ["state"], "")),
            "status": str(pick(row, ["status"], "")),
            "raw": row,
        }
    return hypervisors, errors


def find_hypervisor(hypervisors: dict[str, dict[str, Any]], host: str) -> dict[str, Any]:
    if host in hypervisors:
        return hypervisors[host]
    for name, value in hypervisors.items():
        if name.split(".", 1)[0] == host or host.split(".", 1)[0] == name:
            return value
    return {}


def read_hypervisor_show_vm_count(
    config: dict[str, Any],
    host: str,
    hypervisor: dict[str, Any],
) -> tuple[int | None, str, list[str]]:
    candidates = [
        str(hypervisor.get("hostname", "")),
        str(hypervisor.get("id", "")),
        host,
    ]
    seen: set[str] = set()
    errors: list[str] = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        rows, row_errors = run_openstack_json(
            config,
            f"openstack hypervisor show {shlex.quote(candidate)} -f json",
            timeout=12,
        )
        if row_errors:
            errors.extend(row_errors)
            continue
        if not rows:
            continue
        count = pick_running_vms(rows[0])
        if count is not None:
            return count, f"hypervisor show {candidate}", []
    return None, "", errors


def read_server_count_for_host(config: dict[str, Any], host: str) -> tuple[int | None, str, list[str]]:
    commands = [
        f"openstack server list --all-projects --host {shlex.quote(host)} -f json",
        f"openstack server list --all-projects --long --host {shlex.quote(host)} -f json",
    ]
    errors: list[str] = []
    for command in commands:
        rows, row_errors = run_openstack_json(config, command, timeout=20)
        if row_errors:
            errors.extend(row_errors)
            continue
        return len(rows), "server list --host", []
    return None, "", errors


def resolve_vm_count(
    config: dict[str, Any],
    host: str,
    hypervisor: dict[str, Any],
) -> tuple[int | str, str, list[str]]:
    count = parse_count(hypervisor.get("running_vms"))
    if count is not None:
        return count, str(hypervisor.get("vm_count_source") or "hypervisor list"), []

    count, source, errors = read_hypervisor_show_vm_count(config, host, hypervisor)
    if count is not None:
        return count, source, []

    count, source, server_errors = read_server_count_for_host(config, host)
    if count is not None:
        return count, source, []

    return "unknown", "", [*errors, *server_errors]


def read_resource_providers(config: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    rows, errors = run_openstack_json(config, "openstack resource provider list -f json")
    providers: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(pick(row, ["name"], ""))
        uuid = str(pick(row, ["uuid", "id"], ""))
        if not name:
            continue
        providers[name] = {"name": name, "uuid": uuid, "raw": row}
    return providers, errors


def find_resource_provider(providers: dict[str, dict[str, Any]], host: str, fallback_name: str) -> dict[str, Any]:
    for name in (host, fallback_name):
        if name and name in providers:
            return providers[name]
    for name, value in providers.items():
        if name.split(".", 1)[0] == host:
            return value
    return {}


def read_traits_for_provider(config: dict[str, Any], rp_uuid: str) -> tuple[list[str], list[str]]:
    if not rp_uuid:
        return [], []
    cmd = f"openstack resource provider trait list {shlex.quote(rp_uuid)} -f value -c name"
    result = run_openstack(config, cmd, timeout=12)
    if result["rc"] != 0:
        return [], [result["stderr"] or f"trait list rc={result['rc']}"]
    traits = [line.strip() for line in result["stdout"].splitlines() if line.strip()]
    return traits, []


def read_marker(config: dict[str, Any], host: str, service: str) -> dict[str, Any]:
    marker_path = (
        Path(str(config["KEYLIME_OPENSTACK_STATE_DIR"]))
        / f"{host}.{service}.disabled-by-keylime"
    )
    present = marker_path.is_file()
    content = ""
    if present:
        content = marker_path.read_text(encoding="utf-8", errors="replace")[:4000].strip()
    return {"path": str(marker_path), "present": present, "content": content}


def conclude_node(
    service: dict[str, Any],
    agent_configured: bool,
    decision: dict[str, Any] | None,
    trait_present: bool | None,
    marker: dict[str, Any],
) -> dict[str, str]:
    if service.get("state") != "up":
        return {"level": "bad", "code": "COMPUTE_DOWN", "text": "不可信"}
    if not agent_configured:
        return {"level": "warn", "code": "NO_KEYLIME_AGENT", "text": "未装代理"}
    if service.get("status") != "enabled":
        return {"level": "bad", "code": "COMPUTE_DISABLED", "text": "不可信"}

    result = (decision or {}).get("result")
    if result == "PASS_FRESH" and trait_present is True and marker.get("present") is False:
        return {"level": "ok", "code": "TRUSTED", "text": "可信"}
    if result == "PASS_FRESH":
        return {"level": "bad", "code": "TRUSTED_BUT_OPENSTACK_MISMATCH", "text": "不可信"}
    if result and result != "UNKNOWN":
        return {"level": "bad", "code": "UNTRUSTED", "text": "不可信"}
    return {"level": "bad", "code": "KEYLIME_UNKNOWN", "text": "不可信"}


def build_node(
    config: dict[str, Any],
    service: dict[str, Any],
    decision: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    hypervisors: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    host = service["host"]
    agent_hosts = set(config["AGENT_HOSTS"])
    agent_configured = host in agent_hosts
    agent_ip_by_host = config["AGENT_IP_BY_HOST"]
    hypervisor = find_hypervisor(hypervisors, host)
    provider = find_resource_provider(providers, host, str(config.get("RP_NAME", "")))
    traits, trait_errors = read_traits_for_provider(config, str(provider.get("uuid", "")))
    trusted_trait = str(config["TRUSTED_TRAIT"])
    marker = read_marker(config, host, service["binary"] or str(config["COMPUTE_SERVICE"]))
    node_decision = decision if host == str(config["COMPUTE_HOST"]) and agent_configured else None
    trait_present = trusted_trait in traits if provider.get("uuid") else None
    conclusion = conclude_node(service, agent_configured, node_decision, trait_present, marker)
    node_ip = (
        str(agent_ip_by_host.get(host, ""))
        or str(hypervisor.get("host_ip", ""))
        or "unknown"
    )
    vm_count, vm_count_source, vm_count_errors = resolve_vm_count(config, host, hypervisor)

    return {
        "host": host,
        "ip": node_ip,
        "vm_count": vm_count,
        "vm_count_source": vm_count_source,
        "vm_count_errors": vm_count_errors,
        "zone": service.get("zone", ""),
        "service": service,
        "hypervisor": hypervisor,
        "agent": {
            "configured": agent_configured,
            "ip": str(agent_ip_by_host.get(host, "")),
        },
        "decision": node_decision,
        "placement": {
            "resource_provider": provider.get("name", host),
            "resource_provider_uuid": provider.get("uuid", ""),
            "trusted_trait": trusted_trait,
            "trait_present": trait_present,
            "traits": traits,
            "errors": trait_errors,
        },
        "marker": marker,
        "conclusion": conclusion,
    }


def summarize(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"total": len(nodes), "trusted": 0, "untrusted": 0, "no_agent": 0}
    for node in nodes:
        code = node["conclusion"]["code"]
        level = node["conclusion"]["level"]
        if code == "NO_KEYLIME_AGENT":
            counts["no_agent"] += 1
        elif level == "ok":
            counts["trusted"] += 1
        else:
            counts["untrusted"] += 1
    if not nodes:
        level = "warn"
        text = "未发现 nova-compute 节点"
    elif counts["untrusted"]:
        level = "bad"
        text = "有异常节点"
    elif counts["no_agent"]:
        level = "warn"
        text = "有未装代理节点"
    else:
        level = "ok"
        text = "全部可信"
    return {**counts, "level": level, "text": text}


def collect_status(config: dict[str, Any]) -> dict[str, Any]:
    decision = read_decision(config)
    timer = read_timer()
    services, service_errors = read_compute_services(config)
    hypervisors, hypervisor_errors = read_hypervisors(config)
    providers, provider_errors = read_resource_providers(config)
    nodes = [
        build_node(config, service, decision, providers, hypervisors)
        for service in services
    ]
    errors = [
        *service_errors,
        *hypervisor_errors,
        *provider_errors,
    ]
    if decision.get("reason") == "DECISION_FILE_NOT_FOUND":
        errors.append(decision["reason"])

    return {
        "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "config": {
            "env_file": config["ENV_FILE"],
            "compute_service": config["COMPUTE_SERVICE"],
            "trusted_trait": config["TRUSTED_TRAIT"],
            "keylime_agent_hosts": config["AGENT_HOSTS"],
        },
        "summary": summarize(nodes),
        "timer": timer,
        "nodes": nodes,
        "decision_source": {
            "host": config["COMPUTE_HOST"],
            "decision": decision,
        },
        "errors": errors,
    }


class TrustMonitorHandler(SimpleHTTPRequestHandler):
    config: dict[str, Any] = {}

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.write_json(collect_status(self.config))
            return
        if parsed.path == "/api/health":
            self.write_json({"ok": True})
            return
        super().do_GET()

    def write_json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the read-only Keylime OpenStack trust monitor.")
    parser.add_argument("--host", default=os.environ.get("KEYLIME_MONITOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KEYLIME_MONITOR_PORT", "8088")))
    parser.add_argument(
        "--env-file",
        default=os.environ.get("KEYLIME_OPENSTACK_ENV_FILE", DEFAULT_ENV_FILE),
        help="Path to openstack-keylime-lab.env on csri10.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.chdir(APP_DIR)
    TrustMonitorHandler.config = build_config(args.env_file)
    server = ThreadingHTTPServer((args.host, args.port), TrustMonitorHandler)
    print(f"Serving Keylime trust monitor on http://{args.host}:{args.port}/")
    print("Read-only status API: /api/status")
    server.serve_forever()


if __name__ == "__main__":
    main()
