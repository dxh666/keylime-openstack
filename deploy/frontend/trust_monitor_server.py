#!/usr/bin/env python3
"""Status and TPM PCR policy API for the Keylime + OpenStack console."""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


APP_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = "/etc/keylime-openstack-sync/openstack-keylime-lab.env"
DEFAULTS = {
    "OPENRC": "/etc/kolla/admin-openrc.sh",
    "KEYLIME_DIR": "/opt/keylime-docker",
    "KEYLIME_OPENSTACK_SYNC_DIR": "/opt/keylime-openstack-sync",
    "KEYLIME_OPENSTACK_LOG_DIR": "/var/log",
    "KEYLIME_OPENSTACK_STATE_DIR": "/var/lib/keylime-openstack-sync",
    "KEYLIME_PCR_POLICY_FILE": "",
    "KEYLIME_POLICY_ADMIN_TOKEN": "",
    "KEYLIME_POLICY_APPLY_REACTIVATE": "true",
    "KEYLIME_POLICY_APPLY_SYNC": "false",
    "KEYLIME_POLICY_APPLY_SYNC_MODE": "placement",
    "KEYLIME_STATUS_REFRESH_SECONDS": "3",
    "KEYLIME_VERIFIER_IP": "172.31.100.10",
    "KEYLIME_VERIFIER_PORT": "8881",
    "KEYLIME_REGISTRAR_IP": "172.31.100.10",
    "KEYLIME_REGISTRAR_PORT": "8891",
    "RP_NAME": "csri9",
    "TRUSTED_TRAIT": "CUSTOM_KEYLIME_ATTESTED",
    "COMPUTE_HOST": "csri9",
    "COMPUTE_SERVICE": "nova-compute",
    "KEYLIME_AGENT_UUID_FIXED": "11111111-1111-4111-8111-000000000009",
    "KEYLIME_AGENT_IP": "172.31.100.9",
    "KEYLIME_AGENT_PORT": "9002",
    "KEYLIME_AGENT_API_VERSION": "2.5",
    "KEYLIME_AGENT_INVENTORY_FILE": "/etc/keylime-openstack-sync/keylime-agent-inventory.env",
    "KEYLIME_AGENT_INVENTORY_JSON": "/var/log/keylime-openstack-agent-inventory.json",
    "KEYLIME_AGENT_HOSTS": "",
    "KEYLIME_AGENT_IP_MAP": "",
    "KEYLIME_AGENT_UUID_MAP": "",
    "KEYLIME_VM_COUNT_SLOW_FALLBACK": "false",
    "KEYLIME_TRAIT_SLOW_FALLBACK": "true",
    "KEYLIME_TPM_EVIDENCE_BASELINE_JSON": "/var/log/keylime-openstack-tpm-evidence-baseline.json",
    "KEYLIME_POLICY_BASE_DIR": "/var/lib/keylime-openstack-sync/policies",
    "KEYLIME_POLICY_RENDER_AUDIT_FILE": "/var/log/keylime-openstack-policy-render.json",
    "KEYLIME_POLICY_APPLY_AUDIT_FILE": "/var/log/keylime-openstack-policy-apply.json",
    "KEYLIME_MEASURED_BOOT_MODE": "alert-only",
    "KEYLIME_RUNTIME_GUARD_PATH": "/opt/keylime-cloud-integrity/cloud-runtime-guard.sh",
}

PCR_DIGEST_LENGTHS = {
    "sha1": 40,
    "sha256": 64,
    "sha384": 96,
    "sha512": 128,
}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def expand_env_refs(value: str, env: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group("braced") or match.group("plain") or ""
        return env.get(key, os.environ.get(key, ""))

    return re.sub(r"\$(?:{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))", replace, value)


def load_env_file(path: str, seen: set[str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.is_file():
        return env
    resolved_path = str(env_path.resolve())
    seen = seen or set()
    if resolved_path in seen:
        return env
    seen.add(resolved_path)

    pattern = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$")
    source_pattern = re.compile(r"(?:^|&&\s*)(?:source|\.)\s+(.+?)\s*$")
    for raw_line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        match = pattern.match(line)
        if match:
            key, raw_value = match.groups()
            try:
                parts = shlex.split(raw_value, comments=False, posix=True)
                value = parts[0] if parts else ""
            except ValueError:
                value = raw_value.strip().strip("\"'")
            env[key] = expand_env_refs(value, env)
            continue

        source_match = source_pattern.search(line)
        if not source_match:
            continue
        try:
            parts = shlex.split(source_match.group(1), comments=False, posix=True)
        except ValueError:
            parts = []
        if not parts:
            continue
        source_path = Path(expand_env_refs(parts[0], env))
        if not source_path.is_absolute():
            source_path = env_path.parent / source_path
        env.update(load_env_file(str(source_path), seen))
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
    agent_ip_map = parse_mapping(str(config.get("KEYLIME_AGENT_IP_MAP", "")))
    agent_uuid_map = parse_mapping(str(config.get("KEYLIME_AGENT_UUID_MAP", "")))

    compute_host = str(config.get("COMPUTE_HOST", ""))
    if compute_host:
        if config.get("KEYLIME_AGENT_IP"):
            agent_ip_map.setdefault(compute_host, str(config["KEYLIME_AGENT_IP"]))
        if config.get("KEYLIME_AGENT_UUID_FIXED"):
            agent_uuid_map.setdefault(compute_host, str(config["KEYLIME_AGENT_UUID_FIXED"]))

    for host in [*agent_ip_map.keys(), *agent_uuid_map.keys()]:
        if host and host not in agent_hosts:
            agent_hosts.append(host)
    if not agent_hosts and compute_host:
        agent_hosts = [compute_host]

    config["ENV_FILE"] = env_file
    config["AGENT_HOSTS"] = agent_hosts
    config["AGENT_IP_BY_HOST"] = agent_ip_map
    config["AGENT_UUID_BY_HOST"] = agent_uuid_map
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


def is_truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


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


def run_command(args: list[str], timeout: int = 8, cwd: str | None = None) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
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


def read_decision_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "result": "UNKNOWN", "reason": "DECISION_FILE_NOT_FOUND"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"path": str(path), "result": "UNKNOWN", "reason": f"DECISION_JSON_INVALID: {exc}"}
    data["path"] = str(path)
    return data


def default_decision_path(config: dict[str, Any]) -> Path:
    return Path(str(config["KEYLIME_OPENSTACK_LOG_DIR"])) / "keylime-openstack-sync-decision.json"


def host_decision_path(config: dict[str, Any], host: str) -> Path:
    safe_host = re.sub(r"[^A-Za-z0-9_.-]+", "-", host)
    return Path(str(config["KEYLIME_OPENSTACK_LOG_DIR"])) / f"keylime-openstack-sync-decision-{safe_host}.json"


def read_decision(config: dict[str, Any]) -> dict[str, Any]:
    return read_decision_file(default_decision_path(config))


def read_node_decision(config: dict[str, Any], host: str) -> dict[str, Any]:
    compute_host = str(config.get("COMPUTE_HOST", ""))
    if host == compute_host:
        default_decision = read_decision(config)
        if default_decision.get("reason") != "DECISION_FILE_NOT_FOUND":
            return default_decision

    node_decision = read_decision_file(host_decision_path(config, host))
    if node_decision.get("reason") != "DECISION_FILE_NOT_FOUND":
        return node_decision

    if host == compute_host:
        return read_decision(config)
    return node_decision


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
        running_vms = pick_running_vms(row)
        hypervisors[hostname] = {
            "id": str(pick(row, ["id"], "")),
            "hostname": hostname,
            "host_ip": str(pick(row, ["host_ip", "host ip"], "")),
            "running_vms": running_vms,
            "vm_count_source": "hypervisor list" if running_vms is not None else "",
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


def pick_server_host(row: dict[str, Any]) -> str:
    return str(
        pick(
            row,
            [
                "host",
                "Host",
                "OS-EXT-SRV-ATTR:host",
                "OS-EXT-SRV-ATTR:hypervisor_hostname",
                "hypervisor_hostname",
                "hypervisor hostname",
                "compute_host",
                "compute host",
            ],
            "",
        )
    )


def read_server_counts(config: dict[str, Any]) -> tuple[dict[str, int], str, list[str]]:
    rows, errors = run_openstack_json(
        config,
        "openstack server list --all-projects --long -f json",
        timeout=24,
    )
    if errors:
        return {}, "", errors

    counts: dict[str, int] = {}
    missing_host = 0
    for row in rows:
        host = pick_server_host(row)
        if not host:
            missing_host += 1
            continue
        counts[host] = counts.get(host, 0) + 1

    if rows and missing_host == len(rows):
        return {}, "", ["server list --all-projects --long did not expose a host column"]
    return counts, "server list --all-projects --long", []


def server_count_for_host(server_counts: dict[str, int], host: str) -> int | None:
    if host in server_counts:
        return server_counts[host]
    short_host = host.split(".", 1)[0]
    for key, count in server_counts.items():
        if key.split(".", 1)[0] == short_host:
            return count
    return None


def resolve_vm_count(
    config: dict[str, Any],
    host: str,
    hypervisor: dict[str, Any],
    server_counts: dict[str, int],
    server_count_source: str,
) -> tuple[int | str, str, list[str]]:
    count = parse_count(hypervisor.get("running_vms"))
    if count is not None:
        return count, str(hypervisor.get("vm_count_source") or "hypervisor list"), []

    count = server_count_for_host(server_counts, host)
    if count is not None:
        return count, server_count_source, []

    if not is_truthy(config.get("KEYLIME_VM_COUNT_SLOW_FALLBACK", "false")):
        return "unknown", "", []

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


def read_all_provider_traits(config: dict[str, Any]) -> tuple[dict[str, list[str]], str, list[str]]:
    rows, errors = run_openstack_json(
        config,
        "openstack resource provider trait list --all -f json",
        timeout=18,
    )
    if errors:
        return {}, "", errors

    traits_by_provider: dict[str, list[str]] = {}
    for row in rows:
        provider = str(
            pick(
                row,
                [
                    "resource_provider",
                    "resource provider",
                    "resource_provider_uuid",
                    "resource provider uuid",
                    "uuid",
                    "id",
                    "name",
                ],
                "",
            )
        )
        trait = str(pick(row, ["trait", "name"], ""))
        if not provider or not trait:
            continue
        traits_by_provider.setdefault(provider, []).append(trait)

    if rows and not traits_by_provider:
        return {}, "", ["resource provider trait list --all returned unsupported columns"]
    return traits_by_provider, "resource provider trait list --all", []


def traits_for_provider(
    config: dict[str, Any],
    traits_by_provider: dict[str, list[str]],
    provider: dict[str, Any],
) -> tuple[list[str], str, list[str]]:
    candidates = [
        str(provider.get("uuid", "")),
        str(provider.get("name", "")),
    ]
    for candidate in candidates:
        if candidate and candidate in traits_by_provider:
            return sorted(set(traits_by_provider[candidate])), "resource provider trait list --all", []

    name = str(provider.get("name", ""))
    if name:
        short_name = name.split(".", 1)[0]
        for key, traits in traits_by_provider.items():
            if key.split(".", 1)[0] == short_name:
                return sorted(set(traits)), "resource provider trait list --all", []

    if not is_truthy(config.get("KEYLIME_TRAIT_SLOW_FALLBACK", "true")):
        return [], "", []

    traits, errors = read_traits_for_provider(config, str(provider.get("uuid", "")))
    return traits, "resource provider trait list <uuid>", errors


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
    if decision is None and trait_present is True and marker.get("present") is False:
        return {"level": "ok", "code": "TRUSTED_BY_TRAIT", "text": "可信"}
    if result and result != "UNKNOWN":
        return {"level": "bad", "code": "UNTRUSTED", "text": "不可信"}
    return {"level": "bad", "code": "KEYLIME_UNKNOWN", "text": "不可信"}


def parse_mask_value(value: Any) -> int | None:
    text = str(value or "").strip().strip("\"'")
    if not text:
        return None
    try:
        return int(text, 0)
    except ValueError:
        return None


def mask_has_pcr(value: Any, pcr: int) -> bool | None:
    parsed = parse_mask_value(value)
    if parsed is None:
        return None
    return bool(parsed & (1 << pcr))


def build_trust_layers(
    config: dict[str, Any],
    service: dict[str, Any],
    agent_configured: bool,
    decision: dict[str, Any] | None,
    trait_present: bool | None,
    marker: dict[str, Any],
) -> dict[str, Any]:
    decision = decision or {}
    result = str(decision.get("result") or "")
    event_id = str(decision.get("last_event_id") or "")
    attestation_status = str(decision.get("attestation_status") or "")
    operational_state = str(decision.get("operational_state") or "")
    mask = str(decision.get("tpm_policy_mask") or "")
    has_runtime_policy = decision.get("has_runtime_policy")
    boot_pcr7 = decision.get("boot_pcr7_enforced")
    runtime_pcr10 = decision.get("runtime_pcr10_enforced")

    if boot_pcr7 is None:
        boot_pcr7 = mask_has_pcr(mask, 7)
    if runtime_pcr10 is None:
        runtime_pcr10 = mask_has_pcr(mask, 10)

    if not agent_configured:
        keylime = {
            "level": "warn",
            "code": "NO_AGENT",
            "text": "未装代理",
            "detail": "该计算节点尚未配置 Keylime agent。",
        }
        boot = {**keylime, "text": "未纳管"}
        runtime = {**keylime, "text": "未纳管"}
    elif result == "PASS_FRESH":
        keylime = {
            "level": "ok",
            "code": "PASS_FRESH",
            "text": "证明新鲜",
            "detail": f"attestation_status={attestation_status or 'PASS'}, state={operational_state or '-'}",
        }
        boot = {
            "level": "ok",
            "code": "BOOT_TRUSTED",
            "text": "PCR7 通过" if boot_pcr7 is True else "启动通过",
            "detail": "TPM quote 已通过当前启动策略校验。",
        }
        if has_runtime_policy is False:
            runtime = {
                "level": "warn",
                "code": "RUNTIME_POLICY_UNKNOWN",
                "text": "未确认",
                "detail": "Keylime 状态未显示 runtime policy 已绑定。",
            }
        else:
            runtime = {
                "level": "ok",
                "code": "RUNTIME_TRUSTED",
                "text": "IMA 通过" if runtime_pcr10 is True or has_runtime_policy is True else "运行时通过",
                "detail": "PCR10 / IMA runtime policy 当前未发现偏离。",
            }
    else:
        keylime = {
            "level": "bad",
            "code": "ATTESTATION_NOT_PASS",
            "text": "证明失败",
            "detail": event_id or decision.get("reason") or "Keylime attestation 未通过。",
        }
        boot = {
            "level": "bad",
            "code": "BOOT_OR_TPM_FAILED",
            "text": "未通过",
            "detail": decision.get("reason") or operational_state or "TPM quote 未通过。",
        }
        runtime = {
            "level": "bad" if "ima" in event_id.lower() else "warn",
            "code": "IMA_FAILED" if "ima" in event_id.lower() else "RUNTIME_IMPACTED",
            "text": "IMA 异常" if "ima" in event_id.lower() else "受影响",
            "detail": event_id or "运行时完整性随 Keylime 证明失败进入异常状态。",
        }

    openstack_ok = (
        service.get("state") == "up"
        and service.get("status") == "enabled"
        and trait_present is True
        and marker.get("present") is False
    )
    openstack = {
        "level": "ok" if openstack_ok else "bad",
        "code": "OPENSTACK_TRUSTED" if openstack_ok else "OPENSTACK_RESTRICTED",
        "text": "已准入" if openstack_ok else "已限制",
        "detail": f"trait={trait_present}, service={service.get('status')}/{service.get('state')}, marker={marker.get('present')}",
    }

    measured_mode = str(config.get("KEYLIME_MEASURED_BOOT_MODE", "alert-only"))
    measured_event = event_id.startswith("measured_boot.")
    measured_boot = {
        "level": "warn" if measured_mode == "alert-only" or measured_event else ("ok" if result == "PASS_FRESH" else "bad"),
        "code": "MEASURED_BOOT_ALERT_ONLY" if measured_mode == "alert-only" else "MEASURED_BOOT_ENFORCED",
        "text": "告警观察" if measured_mode == "alert-only" else ("已启用" if result == "PASS_FRESH" else "异常"),
        "detail": "当前环境 measured boot 作为告警信号，不直接隔离节点。" if measured_mode == "alert-only" else (event_id or "Measured boot policy enforced."),
    }

    return {
        "keylime": keylime,
        "boot": boot,
        "runtime": runtime,
        "openstack": openstack,
        "measured_boot": measured_boot,
        "policy": {
            "tpm_policy_mask": mask,
            "has_runtime_policy": has_runtime_policy,
            "boot_pcr7_enforced": boot_pcr7,
            "runtime_pcr10_enforced": runtime_pcr10,
            "runtime_guard_path": str(config.get("KEYLIME_RUNTIME_GUARD_PATH", "")),
        },
    }


def build_node(
    config: dict[str, Any],
    service: dict[str, Any],
    providers: dict[str, dict[str, Any]],
    hypervisors: dict[str, dict[str, Any]],
    server_counts: dict[str, int],
    server_count_source: str,
    traits_by_provider: dict[str, list[str]],
) -> dict[str, Any]:
    host = service["host"]
    agent_hosts = set(config["AGENT_HOSTS"])
    agent_ip_by_host = config["AGENT_IP_BY_HOST"]
    agent_uuid_by_host = config["AGENT_UUID_BY_HOST"]
    agent_configured = host in agent_hosts or host in agent_uuid_by_host
    hypervisor = find_hypervisor(hypervisors, host)
    provider = find_resource_provider(providers, host, str(config.get("RP_NAME", "")))
    traits, trait_source, trait_errors = traits_for_provider(config, traits_by_provider, provider)
    trusted_trait = str(config["TRUSTED_TRAIT"])
    marker = read_marker(config, host, service["binary"] or str(config["COMPUTE_SERVICE"]))
    node_decision = read_node_decision(config, host) if agent_configured else None
    trait_present = trusted_trait in traits if provider.get("uuid") else None
    conclusion = conclude_node(service, agent_configured, node_decision, trait_present, marker)
    trust_layers = build_trust_layers(
        config,
        service,
        agent_configured,
        node_decision,
        trait_present,
        marker,
    )
    node_ip = (
        str(agent_ip_by_host.get(host, ""))
        or str(hypervisor.get("host_ip", ""))
        or "unknown"
    )
    vm_count, vm_count_source, vm_count_errors = resolve_vm_count(
        config,
        host,
        hypervisor,
        server_counts,
        server_count_source,
    )

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
            "uuid": str(agent_uuid_by_host.get(host, "")),
            "port": str(config.get("KEYLIME_AGENT_PORT", "9002")),
        },
        "decision": node_decision,
        "placement": {
            "resource_provider": provider.get("name", host),
            "resource_provider_uuid": provider.get("uuid", ""),
            "trusted_trait": trusted_trait,
            "trait_present": trait_present,
            "traits": traits,
            "trait_source": trait_source,
            "errors": trait_errors,
        },
        "marker": marker,
        "conclusion": conclusion,
        "trust_layers": trust_layers,
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
        text = "存在异常节点"
    elif counts["no_agent"]:
        level = "warn"
        text = "存在未装代理节点"
    else:
        level = "ok"
        text = "全部可信"
    return {**counts, "level": level, "text": text}


def collect_status(config: dict[str, Any]) -> dict[str, Any]:
    decision = read_decision(config)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            "timer": executor.submit(read_timer),
            "services": executor.submit(read_compute_services, config),
            "hypervisors": executor.submit(read_hypervisors, config),
            "server_counts": executor.submit(read_server_counts, config),
            "providers": executor.submit(read_resource_providers, config),
            "traits": executor.submit(read_all_provider_traits, config),
        }
        timer = futures["timer"].result()
        services, service_errors = futures["services"].result()
        hypervisors, hypervisor_errors = futures["hypervisors"].result()
        server_counts, server_count_source, server_count_errors = futures["server_counts"].result()
        providers, provider_errors = futures["providers"].result()
        traits_by_provider, trait_source, trait_errors = futures["traits"].result()

    nodes = [
        build_node(
            config,
            service,
            providers,
            hypervisors,
            server_counts,
            server_count_source,
            traits_by_provider,
        )
        for service in services
    ]
    errors = [
        *service_errors,
        *hypervisor_errors,
        *server_count_errors,
        *provider_errors,
    ]
    if trait_errors and not is_truthy(config.get("KEYLIME_TRAIT_SLOW_FALLBACK", "true")):
        errors.extend(trait_errors)
    if decision.get("reason") == "DECISION_FILE_NOT_FOUND":
        errors.append(decision["reason"])

    return {
        "checked_at_utc": utc_now(),
        "config": {
            "env_file": config["ENV_FILE"],
            "compute_service": config["COMPUTE_SERVICE"],
            "trusted_trait": config["TRUSTED_TRAIT"],
            "keylime_agent_inventory_file": config.get("KEYLIME_AGENT_INVENTORY_FILE", ""),
            "keylime_agent_hosts": config["AGENT_HOSTS"],
            "keylime_agent_uuid_map": config["AGENT_UUID_BY_HOST"],
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


class StatusCache:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        try:
            self.interval = max(1.0, float(str(config.get("KEYLIME_STATUS_REFRESH_SECONDS", "3"))))
        except ValueError:
            self.interval = 3.0
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.payload: dict[str, Any] | None = None
        self.generated_monotonic = 0.0
        self.last_refresh_duration = 0.0
        self.refreshing = False
        self.error = ""
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._loop, name="keylime-status-refresh", daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(self.interval)

    def refresh(self) -> None:
        with self.lock:
            if self.refreshing:
                return
            self.refreshing = True
        try:
            started = time.monotonic()
            payload = collect_status(self.config)
            duration = time.monotonic() - started
            with self.lock:
                self.payload = payload
                self.generated_monotonic = time.monotonic()
                self.last_refresh_duration = duration
                self.error = ""
        except Exception as exc:  # pragma: no cover - long-running service guard
            with self.lock:
                self.error = str(exc)
        finally:
            with self.lock:
                self.refreshing = False

    def get(self, force: bool = False) -> dict[str, Any]:
        if force or self.payload is None:
            self.refresh()

        with self.lock:
            if self.payload is None:
                payload = {
                    "checked_at_utc": utc_now(),
                    "summary": {
                        "total": 0,
                        "trusted": 0,
                        "untrusted": 0,
                        "no_agent": 0,
                        "level": "warn",
                        "text": "状态采集中",
                    },
                    "nodes": [],
                    "errors": [self.error] if self.error else [],
                }
                generated_monotonic = time.monotonic()
            else:
                payload = copy.deepcopy(self.payload)
                generated_monotonic = self.generated_monotonic

            payload["cache"] = {
                "enabled": True,
                "refresh_interval_seconds": self.interval,
                "age_seconds": max(0, round(time.monotonic() - generated_monotonic, 1)),
                "last_refresh_duration_seconds": round(self.last_refresh_duration, 2),
                "refreshing": self.refreshing,
                "last_error": self.error,
            }
            if self.error:
                payload.setdefault("errors", []).append(self.error)
            return payload


def policy_file_path(config: dict[str, Any]) -> Path:
    configured = str(config.get("KEYLIME_PCR_POLICY_FILE", "")).strip()
    if configured:
        return Path(configured)
    return Path(str(config["KEYLIME_OPENSTACK_STATE_DIR"])) / "tpm-pcr-policies.json"


def default_policy_store() -> dict[str, Any]:
    return {"version": 1, "policies": [], "bindings": {}, "events": []}


def load_policy_store(config: dict[str, Any]) -> dict[str, Any]:
    path = policy_file_path(config)
    if not path.is_file():
        return default_policy_store()
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, f"策略文件 JSON 无效: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, "策略文件格式无效")
    parsed.setdefault("version", 1)
    parsed.setdefault("policies", [])
    parsed.setdefault("bindings", {})
    parsed.setdefault("events", [])
    if not isinstance(parsed["policies"], list):
        parsed["policies"] = []
    if not isinstance(parsed["bindings"], dict):
        parsed["bindings"] = {}
    if not isinstance(parsed["events"], list):
        parsed["events"] = []
    return parsed


def save_policy_store(config: dict[str, Any], store: dict[str, Any]) -> None:
    path = policy_file_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug or "keylime-policy"


def policy_module_key(policy_or_payload: dict[str, Any] | None) -> str:
    if not isinstance(policy_or_payload, dict):
        return "boot"
    policy_type = str(policy_or_payload.get("type", "")).strip()
    module = str(policy_or_payload.get("module", "")).strip()
    if policy_type == "ima_runtime" or module == "runtime_integrity":
        return "runtime"
    return "boot"


def normalize_list_field(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    lines: list[str] = []
    for chunk in str(value).replace(",", "\n").splitlines():
        item = chunk.strip()
        if item:
            lines.append(item)
    return lines


def get_module_binding(bindings: dict[str, Any], host: str, module_key: str) -> dict[str, Any]:
    binding = bindings.get(host, {})
    if not isinstance(binding, dict):
        return {}
    if module_key in ("boot", "runtime") and isinstance(binding.get(module_key), dict):
        return binding[module_key]
    if module_key == "boot" and binding.get("policy_id"):
        return binding
    return {}


def set_module_binding(bindings: dict[str, Any], host: str, module_key: str, record: dict[str, Any]) -> None:
    current = bindings.get(host, {})
    if not isinstance(current, dict) or (current.get("policy_id") and not current.get("boot")):
        current = {"boot": current} if isinstance(current, dict) and current.get("policy_id") else {}
    current[module_key] = record
    bindings[host] = current


def delete_policy_bindings(bindings: dict[str, Any], policy_id: str) -> None:
    for host, binding in list(bindings.items()):
        if not isinstance(binding, dict):
            continue
        if binding.get("policy_id") == policy_id:
            del bindings[host]
            continue
        for module_key in ("boot", "runtime"):
            module_binding = binding.get(module_key)
            if isinstance(module_binding, dict) and module_binding.get("policy_id") == policy_id:
                del binding[module_key]
        if not binding:
            del bindings[host]


def normalize_digest(value: Any, hash_alg: str) -> str:
    text = str(value).strip()
    if text.lower().startswith("0x"):
        text = text[2:]
    text = text.upper()
    expected_len = PCR_DIGEST_LENGTHS[hash_alg]
    if not re.fullmatch(r"[0-9A-F]+", text):
        raise ApiError(HTTPStatus.BAD_REQUEST, "PCR 摘要必须是十六进制字符串")
    if len(text) != expected_len:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{hash_alg} PCR 摘要长度应为 {expected_len} 个十六进制字符")
    return text


def normalize_pcr_index(value: Any) -> str:
    text = str(value).strip()
    if not re.fullmatch(r"\d+", text):
        raise ApiError(HTTPStatus.BAD_REQUEST, "PCR 编号必须是 0-23 的整数")
    idx = int(text)
    if idx < 0 or idx > 23:
        raise ApiError(HTTPStatus.BAD_REQUEST, "PCR 编号必须是 0-23 的整数")
    return str(idx)


def calculate_mask(pcrs: dict[str, str]) -> str:
    mask = 0
    for key in pcrs:
        mask |= 1 << int(key)
    return hex(mask)


def build_tpm_policy(pcrs: dict[str, str]) -> dict[str, Any]:
    policy: dict[str, Any] = {"mask": calculate_mask(pcrs)}
    for key, digest in sorted(pcrs.items(), key=lambda item: int(item[0])):
        policy[key] = [str(digest).lower()]
    return policy


def normalize_pcrs(payload: Any, hash_alg: str) -> dict[str, str]:
    pcrs: dict[str, str] = {}
    if isinstance(payload, dict):
        items = payload.items()
    elif isinstance(payload, list):
        items = []
        for item in payload:
            if not isinstance(item, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "PCR 列表项格式无效")
            items.append((item.get("index"), item.get("digest")))
    else:
        raise ApiError(HTTPStatus.BAD_REQUEST, "PCR 策略必须包含 pcrs 字段")

    for raw_index, raw_digest in items:
        index = normalize_pcr_index(raw_index)
        digest = normalize_digest(raw_digest, hash_alg)
        pcrs[index] = digest

    if not pcrs:
        raise ApiError(HTTPStatus.BAD_REQUEST, "至少需要设置一个 PCR")
    return dict(sorted(pcrs.items(), key=lambda item: int(item[0])))


def normalize_boot_policy_payload(
    payload: dict[str, Any],
    existing: dict[str, Any] | None,
    policy_id: str,
    name: str,
    now: str,
    created_at: str,
) -> dict[str, Any]:
    hash_alg = str(payload.get("hash_alg") or (existing or {}).get("hash_alg") or "sha256").lower()
    if hash_alg not in PCR_DIGEST_LENGTHS:
        raise ApiError(HTTPStatus.BAD_REQUEST, "hash_alg 仅支持 sha1、sha256、sha384、sha512")

    pcrs_source = payload.get("pcrs", (existing or {}).get("pcrs", {}))
    pcrs = normalize_pcrs(pcrs_source, hash_alg)
    module = str(payload.get("module") or (existing or {}).get("module") or "boot_measurement").strip()
    if module == "runtime_integrity":
        module = "boot_measurement"
    if policy_id.startswith("bad-") or module == "boot_measurement_negative_test":
        raise ApiError(HTTPStatus.BAD_REQUEST, "管理系统不允许保存非生产策略")

    return {
        "id": policy_id,
        "name": name,
        "description": str(payload.get("description") or (existing or {}).get("description") or "").strip(),
        "type": "tpm_pcr",
        "module": module,
        "hash_alg": hash_alg,
        "pcrs": pcrs,
        "mask": calculate_mask(pcrs),
        "tpm_policy": build_tpm_policy(pcrs),
        "created_at_utc": created_at,
        "updated_at_utc": now,
    }


def normalize_runtime_policy_payload(
    payload: dict[str, Any],
    existing: dict[str, Any] | None,
    policy_id: str,
    name: str,
    now: str,
    created_at: str,
) -> dict[str, Any]:
    runtime_policy_name = str(
        payload.get("runtime_policy_name")
        or (existing or {}).get("runtime_policy_name")
        or policy_id
    ).strip()
    runtime_policy_path = str(
        payload.get("runtime_policy_path")
        or (existing or {}).get("runtime_policy_path")
        or ""
    ).strip()
    if not runtime_policy_name:
        raise ApiError(HTTPStatus.BAD_REQUEST, "runtime policy 名称不能为空")
    if not runtime_policy_path:
        raise ApiError(HTTPStatus.BAD_REQUEST, "runtime policy 文件路径不能为空")

    return {
        "id": policy_id,
        "name": name,
        "description": str(payload.get("description") or (existing or {}).get("description") or "").strip(),
        "type": "ima_runtime",
        "module": "runtime_integrity",
        "runtime_policy_name": runtime_policy_name,
        "runtime_policy_path": runtime_policy_path,
        "protected_paths": normalize_list_field(payload.get("protected_paths", (existing or {}).get("protected_paths", []))),
        "excludes": normalize_list_field(payload.get("excludes", (existing or {}).get("excludes", []))),
        "created_at_utc": created_at,
        "updated_at_utc": now,
    }


def normalize_policy_payload(payload: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")

    name = str(payload.get("name") or (existing or {}).get("name") or "").strip()
    if not name:
        raise ApiError(HTTPStatus.BAD_REQUEST, "策略名称不能为空")

    policy_id = slugify(str(payload.get("id") or (existing or {}).get("id") or name))
    now = utc_now()
    created_at = str((existing or {}).get("created_at_utc") or now)
    if policy_id.startswith("bad-"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "管理系统不允许保存非生产策略")

    if policy_module_key({**(existing or {}), **payload}) == "runtime":
        return normalize_runtime_policy_payload(payload, existing, policy_id, name, now, created_at)
    return normalize_boot_policy_payload(payload, existing, policy_id, name, now, created_at)


def find_policy(store: dict[str, Any], policy_id: str) -> dict[str, Any] | None:
    for policy in store.get("policies", []):
        if isinstance(policy, dict) and policy.get("id") == policy_id:
            return policy
    return None


def is_management_policy(policy: dict[str, Any]) -> bool:
    if str(policy.get("module", "")) == "boot_measurement_negative_test":
        return False
    if str(policy.get("id", "")).startswith("bad-"):
        return False
    source = policy.get("source", {})
    if isinstance(source, dict) and source.get("mode") == "negative-test":
        return False
    return True


def upsert_policy(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    store = load_policy_store(config)
    policy_id = slugify(str(payload.get("id") or payload.get("name") or ""))
    existing = find_policy(store, policy_id)
    policy = normalize_policy_payload(payload, existing=existing)
    replaced = False
    policies = []
    for item in store.get("policies", []):
        if isinstance(item, dict) and item.get("id") == policy["id"]:
            policies.append(policy)
            replaced = True
        else:
            policies.append(item)
    if not replaced:
        policies.append(policy)
    policies.sort(key=lambda item: str(item.get("name", "")))
    store["policies"] = policies
    store.setdefault("events", []).append(
        {
            "at_utc": utc_now(),
            "action": "policy_saved",
            "policy_id": policy["id"],
            "policy_name": policy["name"],
        }
    )
    store["events"] = store["events"][-80:]
    save_policy_store(config, store)
    return {"ok": True, "policy": policy, "store_path": str(policy_file_path(config))}


def delete_policy(config: dict[str, Any], policy_id: str) -> dict[str, Any]:
    store = load_policy_store(config)
    before = len(store.get("policies", []))
    store["policies"] = [
        item
        for item in store.get("policies", [])
        if not (isinstance(item, dict) and item.get("id") == policy_id)
    ]
    if len(store["policies"]) == before:
        raise ApiError(HTTPStatus.NOT_FOUND, f"策略不存在: {policy_id}")
    delete_policy_bindings(store.setdefault("bindings", {}), policy_id)
    store.setdefault("events", []).append(
        {"at_utc": utc_now(), "action": "policy_deleted", "policy_id": policy_id}
    )
    store["events"] = store["events"][-80:]
    save_policy_store(config, store)
    return {"ok": True, "policy_id": policy_id, "store_path": str(policy_file_path(config))}


def policy_templates() -> list[dict[str, Any]]:
    return [
        {
            "id": "sha256-pcr7-secure-boot",
            "name": "SHA256 PCR7 启动策略",
            "description": "用于校验 Secure Boot / 启动状态相关 PCR7，先填入目标节点当前 PCR7 摘要。",
            "module": "boot_measurement",
            "hash_alg": "sha256",
            "pcrs": {"7": "0" * 64},
        },
        {
            "id": "sha256-pcr0-7-boot-baseline",
            "name": "SHA256 PCR0-7 启动基线",
            "description": "用于记录节点启动链 PCR0-7 基线。创建后请替换为真实 PCR 摘要。",
            "module": "boot_measurement_diagnostic",
            "hash_alg": "sha256",
            "pcrs": {str(i): "0" * 64 for i in range(8)},
        },
        {
            "id": "ima-runtime-policy-file",
            "name": "IMA 运行时策略文件",
            "description": "引用由 Keylime 官方 runtime policy 工具生成的 JSON 文件，按节点绑定后下发。",
            "type": "ima_runtime",
            "module": "runtime_integrity",
            "runtime_policy_name": "cloud-runtime-guard",
            "runtime_policy_path": "/var/lib/keylime-openstack-sync/policies/runtime/cloud-runtime-policy.json",
            "protected_paths": ["/opt/keylime-cloud-integrity/cloud-runtime-guard.sh"],
            "excludes": ["^(?!(boot_aggregate|/opt/keylime-cloud-integrity/cloud-runtime-guard.sh)$).*"],
        },
    ]


def collect_policy_overview(config: dict[str, Any], status: dict[str, Any] | None = None) -> dict[str, Any]:
    store = load_policy_store(config)
    if status is None:
        status = collect_status(config)
    bindings = store.get("bindings", {})
    policies = []
    for item in store.get("policies", []):
        if not isinstance(item, dict):
            continue
        policy = dict(item)
        if not is_management_policy(policy):
            continue
        policy["module_key"] = policy_module_key(policy)
        if policy.get("type") == "tpm_pcr" and isinstance(policy.get("pcrs"), dict):
            policy["mask"] = calculate_mask(policy["pcrs"])
            policy["tpm_policy"] = build_tpm_policy(policy["pcrs"])
        if policy.get("type") == "tpm_pcr":
            policy["tpm_policy_json"] = json.dumps(policy.get("tpm_policy", {}), ensure_ascii=False, sort_keys=True)
        policies.append(policy)
    management_policy_ids = {str(policy.get("id", "")) for policy in policies}

    nodes = []
    for node in status.get("nodes", []):
        host = str(node.get("host", ""))
        boot_binding = get_module_binding(bindings, host, "boot")
        runtime_binding = get_module_binding(bindings, host, "runtime")
        if str(boot_binding.get("policy_id", "")) not in management_policy_ids:
            boot_binding = {}
        if str(runtime_binding.get("policy_id", "")) not in management_policy_ids:
            runtime_binding = {}
        node_summary = {
            "host": host,
            "ip": node.get("ip", ""),
            "trust_text": node.get("conclusion", {}).get("text", "未知"),
            "trust_level": node.get("conclusion", {}).get("level", "warn"),
            "service_state": node.get("service", {}).get("state", ""),
            "service_status": node.get("service", {}).get("status", ""),
            "agent": node.get("agent", {}),
            "decision": node.get("decision", {}),
            "placement": node.get("placement", {}),
            "marker": node.get("marker", {}),
            "trust_layers": node.get("trust_layers", {}),
            "bindings": {
                "boot": boot_binding if isinstance(boot_binding, dict) else {},
                "runtime": runtime_binding if isinstance(runtime_binding, dict) else {},
            },
            "binding": boot_binding if isinstance(boot_binding, dict) else {},
            "can_apply": bool(node.get("agent", {}).get("uuid") and node.get("ip") not in ("", "unknown")),
        }
        nodes.append(node_summary)

    return {
        "checked_at_utc": utc_now(),
        "store_path": str(policy_file_path(config)),
        "policies": policies,
        "bindings": bindings,
        "events": store.get("events", [])[-30:],
        "nodes": nodes,
        "templates": policy_templates(),
        "baseline": collect_tpm_baseline_summary(config),
        "audit_files": collect_policy_audit_files(config),
        "errors": status.get("errors", []),
    }


def read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def baseline_file_path(config: dict[str, Any]) -> Path:
    return Path(str(config.get("KEYLIME_TPM_EVIDENCE_BASELINE_JSON", "/var/log/keylime-openstack-tpm-evidence-baseline.json")))


def collect_tpm_baseline_summary(config: dict[str, Any]) -> dict[str, Any]:
    path = baseline_file_path(config)
    data = read_json_file(path)
    if not data:
        return {"ok": False, "path": str(path), "message": "TPM evidence baseline not found or invalid"}

    nodes = []
    for node in data.get("nodes", []):
        pcrs = node.get("sha256_pcr_0_7", {}) if isinstance(node, dict) else {}
        nodes.append(
            {
                "host": node.get("host", ""),
                "ip": node.get("ip", ""),
                "ssh_rc": node.get("ssh_rc"),
                "sha256_pcr_count": len(pcrs),
                "pcr7": pcrs.get("7", ""),
                "has_event_log": node.get("has_event_log"),
                "has_ima_ascii_log": node.get("has_ima_ascii_log"),
                "has_ima_binary_log": node.get("has_ima_binary_log"),
                "keylime_agent_container_seen": node.get("keylime_agent_container_seen"),
            }
        )
    return {
        "ok": True,
        "path": str(path),
        "checked_at_utc": data.get("checked_at_utc"),
        "raw_dir": data.get("raw_dir"),
        "nodes": nodes,
        "recommended_bind_mode": "pcr7",
    }


def collect_policy_audit_files(config: dict[str, Any]) -> dict[str, Any]:
    files = {
        "render": Path(str(config.get("KEYLIME_POLICY_RENDER_AUDIT_FILE", "/var/log/keylime-openstack-policy-render.json"))),
        "apply": Path(str(config.get("KEYLIME_POLICY_APPLY_AUDIT_FILE", "/var/log/keylime-openstack-policy-apply.json"))),
    }
    result: dict[str, Any] = {}
    for name, path in files.items():
        item: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            parsed = read_json_file(path)
            if parsed:
                item["checked_at_utc"] = parsed.get("checked_at_utc") or parsed.get("rendered_at_utc")
                item["summary"] = {
                    "rendered": len(parsed.get("rendered", [])) if isinstance(parsed.get("rendered"), list) else None,
                    "applied": len(parsed.get("applied", [])) if isinstance(parsed.get("applied"), list) else None,
                    "failed": len(parsed.get("failed", [])) if isinstance(parsed.get("failed"), list) else None,
                }
        result[name] = item
    return result


def upsert_policy_record(store: dict[str, Any], policy: dict[str, Any]) -> None:
    policies = []
    replaced = False
    for item in store.get("policies", []):
        if isinstance(item, dict) and item.get("id") == policy["id"]:
            if item.get("created_at_utc"):
                policy["created_at_utc"] = item["created_at_utc"]
            policies.append(policy)
            replaced = True
        else:
            policies.append(item)
    if not replaced:
        policies.append(policy)
    policies.sort(key=lambda item: str(item.get("id", "")))
    store["policies"] = policies


def make_policy_record(
    policy_id: str,
    name: str,
    description: str,
    pcrs: dict[str, str],
    source: dict[str, Any] | None = None,
    module: str = "boot_measurement",
) -> dict[str, Any]:
    now = utc_now()
    normalized = {str(k): normalize_digest(v, "sha256") for k, v in pcrs.items()}
    return {
        "id": slugify(policy_id),
        "name": name,
        "description": description,
        "type": "tpm_pcr",
        "module": module,
        "hash_alg": "sha256",
        "pcrs": dict(sorted(normalized.items(), key=lambda item: int(item[0]))),
        "mask": calculate_mask(normalized),
        "tpm_policy": build_tpm_policy(normalized),
        "source": source or {},
        "created_at_utc": now,
        "updated_at_utc": now,
    }


def import_baseline_policies(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    baseline_path = Path(str(payload.get("baseline_path") or baseline_file_path(config)))
    bind_mode = str(payload.get("bind_mode") or "pcr7").strip()
    baseline = read_json_file(baseline_path)
    if not baseline:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"TPM evidence baseline not readable or invalid: {baseline_path}")

    store = load_policy_store(config)
    rendered = []
    warnings = []
    for node in baseline.get("nodes", []):
        if not isinstance(node, dict):
            continue
        host = str(node.get("host") or "").strip()
        values = {str(k): str(v).upper() for k, v in node.get("sha256_pcr_0_7", {}).items()}
        missing = [str(i) for i in range(8) if not values.get(str(i))]
        if not host or missing:
            warnings.append({"host": host, "missing": missing})
            continue

        pcr7_policy = make_policy_record(
            f"{host}-sha256-pcr7-baseline",
            f"{host} SHA256 PCR7 baseline",
            f"Generated from TPM evidence baseline {baseline.get('checked_at_utc')}. Stable boot-measurement policy.",
            {"7": values["7"]},
            {"baseline": str(baseline_path), "host": host, "mode": "pcr7"},
            module="boot_measurement",
        )
        pcr0_7_policy = make_policy_record(
            f"{host}-sha256-pcr0-7-exact",
            f"{host} SHA256 PCR0-7 exact baseline",
            "Generated from TPM evidence baseline for controlled boot-measurement rollout.",
            {str(i): values[str(i)] for i in range(8)},
            {"baseline": str(baseline_path), "host": host, "mode": "pcr0-7"},
            module="boot_measurement_diagnostic",
        )
        upsert_policy_record(store, pcr7_policy)
        upsert_policy_record(store, pcr0_7_policy)

        if bind_mode in ("pcr7", "pcr0-7"):
            bound = pcr7_policy if bind_mode == "pcr7" else pcr0_7_policy
            set_module_binding(store.setdefault("bindings", {}), host, "boot", {
                "host": host,
                "policy_id": bound["id"],
                "policy_name": bound["name"],
                "policy_type": bound["type"],
                "binding_mode": bind_mode,
                "bound_at_utc": utc_now(),
                "source": "frontend-import-baseline",
            })
        rendered.append({"host": host, "pcr7_policy_id": pcr7_policy["id"], "pcr0_7_policy_id": pcr0_7_policy["id"]})

    event = {
        "at_utc": utc_now(),
        "action": "policies_imported_from_tpm_baseline",
        "baseline": str(baseline_path),
        "bind_mode": bind_mode,
        "rendered_count": len(rendered),
    }
    store.setdefault("events", []).append(event)
    store["events"] = store.get("events", [])[-120:]
    save_policy_store(config, store)

    return {
        "ok": True,
        "baseline_path": str(baseline_path),
        "store_path": str(policy_file_path(config)),
        "bind_mode": bind_mode,
        "rendered": rendered,
        "warnings": warnings,
    }


def run_keylime_tenant(
    config: dict[str, Any],
    args: list[str],
    timeout: int = 180,
    volumes: list[tuple[str, str, str]] | None = None,
) -> dict[str, Any]:
    keylime_dir = Path(str(config["KEYLIME_DIR"]))
    if not keylime_dir.is_dir():
        return {"rc": 1, "stdout": "", "stderr": f"Keylime docker directory not found: {keylime_dir}"}
    command = ["docker", "compose", "run", "--rm"]
    for host_path, container_path, mode in volumes or []:
        command.extend(["-v", f"{host_path}:{container_path}:{mode}"])
    command.extend(["keylime-tenant", *args])
    return run_command(command, timeout=timeout, cwd=str(keylime_dir))


def run_policy_sync(config: dict[str, Any], mode: str = "placement") -> dict[str, Any]:
    sync_dir = Path(str(config.get("KEYLIME_OPENSTACK_SYNC_DIR", "/opt/keylime-openstack-sync")))
    script_name = "keylime-sync-control-loop.sh" if mode == "control-loop" else "keylime-placement-sync.sh"
    script = sync_dir / script_name
    if not script.is_file():
        return {"rc": 1, "stdout": "", "stderr": f"sync script not found: {script}"}
    return run_command([str(script)], timeout=240)


def tail_text(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def apply_policy(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")

    policy_id = str(payload.get("policy_id", "")).strip()
    if not policy_id:
        raise ApiError(HTTPStatus.BAD_REQUEST, "policy_id 不能为空")

    store = load_policy_store(config)
    policy = find_policy(store, policy_id)
    if not policy:
        raise ApiError(HTTPStatus.NOT_FOUND, f"策略不存在: {policy_id}")
    if not is_management_policy(policy):
        raise ApiError(HTTPStatus.BAD_REQUEST, "管理系统不允许下发非生产策略")
    module_key = policy_module_key(policy)

    status = collect_status(config)
    nodes_by_host = {node["host"]: node for node in status.get("nodes", [])}
    requested_hosts = [str(host).strip() for host in payload.get("hosts", []) if str(host).strip()]
    if payload.get("all"):
        requested_hosts = [
            host for host, node in nodes_by_host.items()
            if node.get("agent", {}).get("uuid")
        ]
    if not requested_hosts:
        raise ApiError(HTTPStatus.BAD_REQUEST, "至少选择一个计算节点")

    tpm_policy: dict[str, Any] | None = None
    tpm_policy_json = ""
    runtime_policy_path: Path | None = None
    runtime_container_path = ""
    runtime_volumes: list[tuple[str, str, str]] = []
    if module_key == "runtime":
        runtime_policy_path = Path(str(policy.get("runtime_policy_path", "")))
        if not runtime_policy_path.is_file():
            raise ApiError(HTTPStatus.BAD_REQUEST, f"runtime policy 文件不可读: {runtime_policy_path}")
        runtime_container_path = f"/keylime-runtime-policy/{runtime_policy_path.name}"
        runtime_volumes = [(str(runtime_policy_path.parent), "/keylime-runtime-policy", "ro")]
    else:
        tpm_policy = build_tpm_policy(policy.get("pcrs", {}))
        # Keylime tenant accepts PCR numbers as policy keys; mask is kept for local display only.
        tpm_policy_for_tenant = dict(tpm_policy)
        tpm_policy_for_tenant.pop("mask", None)
        tpm_policy_json = json.dumps(tpm_policy_for_tenant, separators=(",", ":"), sort_keys=True)
    verifier_ip = str(config["KEYLIME_VERIFIER_IP"])
    verifier_port = str(config["KEYLIME_VERIFIER_PORT"])
    registrar_ip = str(config["KEYLIME_REGISTRAR_IP"])
    registrar_port = str(config["KEYLIME_REGISTRAR_PORT"])
    agent_port = str(config.get("KEYLIME_AGENT_PORT", "9002"))
    agent_api_version = str(config.get("KEYLIME_AGENT_API_VERSION", "2.5"))
    should_reactivate = is_truthy(config.get("KEYLIME_POLICY_APPLY_REACTIVATE", "true"))
    should_sync = is_truthy(payload.get("sync", config.get("KEYLIME_POLICY_APPLY_SYNC", "false")))
    sync_mode = str(payload.get("sync_mode") or config.get("KEYLIME_POLICY_APPLY_SYNC_MODE", "placement"))
    results = []

    for host in requested_hosts:
        node = nodes_by_host.get(host)
        if not node:
            results.append({"host": host, "status": "failed", "message": "OpenStack 中未发现该计算节点"})
            continue
        agent = node.get("agent", {})
        agent_uuid = str(agent.get("uuid", "")).strip()
        agent_ip = str(agent.get("ip") or node.get("ip") or "").strip()
        if not agent_uuid:
            results.append({"host": host, "status": "failed", "message": "缺少 agent UUID，请配置 KEYLIME_AGENT_UUID_MAP"})
            continue
        if not agent_ip or agent_ip == "unknown":
            results.append({"host": host, "status": "failed", "message": "缺少 agent IP"})
            continue

        common = [
            "-u", agent_uuid,
            "-v", verifier_ip,
            "-vp", verifier_port,
            "-r", registrar_ip,
            "-rp", registrar_port,
        ]
        update_args = [
                "-c", "update",
                "-t", agent_ip,
                "-tp", agent_port,
                *common,
                "--agent-api-version", agent_api_version,
        ]
        if module_key == "runtime":
            update_args.extend(
                [
                    "--runtime-policy-name", str(policy.get("runtime_policy_name") or policy["id"]),
                    "--runtime-policy", runtime_container_path,
                ]
            )
        else:
            update_args.extend(["--tpm_policy", tpm_policy_json])

        update_result = run_keylime_tenant(
            config,
            update_args,
            timeout=240,
            volumes=runtime_volumes,
        )
        reactivate_result = {"rc": 0, "stdout": "", "stderr": "reactivate skipped"}
        if update_result["rc"] == 0 and should_reactivate:
            reactivate_result = run_keylime_tenant(
                config,
                ["-c", "reactivate", *common],
                timeout=120,
            )

        sync_result = {"rc": 0, "stdout": "", "stderr": "sync skipped"}
        if update_result["rc"] == 0 and reactivate_result["rc"] == 0 and should_sync:
            sync_result = run_policy_sync(config, mode=sync_mode)

        success = update_result["rc"] == 0 and reactivate_result["rc"] == 0 and sync_result["rc"] == 0
        record = {
            "host": host,
            "agent_uuid": agent_uuid,
            "agent_ip": agent_ip,
            "policy_id": policy["id"],
            "policy_name": policy["name"],
            "policy_type": policy.get("type", "tpm_pcr"),
            "policy_module": module_key,
            "tpm_policy": tpm_policy,
            "runtime_policy_name": policy.get("runtime_policy_name", ""),
            "runtime_policy_path": str(runtime_policy_path or ""),
            "status": "success" if success else "failed",
            "applied_at_utc": utc_now(),
            "update_rc": update_result["rc"],
            "reactivate_rc": reactivate_result["rc"],
            "sync_rc": sync_result["rc"],
            "stdout": tail_text("\n".join(
                part for part in [
                    update_result["stdout"],
                    reactivate_result["stdout"],
                    sync_result["stdout"],
                ] if part
            )),
            "stderr": tail_text("\n".join(
                part for part in [
                    update_result["stderr"],
                    reactivate_result["stderr"],
                    sync_result["stderr"],
                ] if part and part not in ("reactivate skipped", "sync skipped")
            )),
        }
        set_module_binding(store.setdefault("bindings", {}), host, module_key, record)
        store.setdefault("events", []).append(
            {
                "at_utc": record["applied_at_utc"],
                "action": "policy_applied",
                "host": host,
                "policy_id": policy["id"],
                "status": record["status"],
            }
        )
        results.append(record)

    store["events"] = store.get("events", [])[-80:]
    save_policy_store(config, store)
    return {"ok": all(item.get("status") == "success" for item in results), "results": results}


def apply_bound_policies(config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ApiError(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")

    store = load_policy_store(config)
    status = collect_status(config)
    nodes_by_host = {node["host"]: node for node in status.get("nodes", [])}
    module_key = str(payload.get("module") or "boot").strip()
    if module_key not in ("boot", "runtime"):
        raise ApiError(HTTPStatus.BAD_REQUEST, "module 仅支持 boot 或 runtime")
    requested_hosts = [str(host).strip() for host in payload.get("hosts", []) if str(host).strip()]
    if payload.get("all"):
        requested_hosts = []
        for host in nodes_by_host:
            policy_id = str(get_module_binding(store.get("bindings", {}), host, module_key).get("policy_id", ""))
            policy = find_policy(store, policy_id) if policy_id else None
            if policy and is_management_policy(policy):
                requested_hosts.append(host)
    if not requested_hosts:
        raise ApiError(HTTPStatus.BAD_REQUEST, "至少选择一个已绑定策略的计算节点")

    results: list[dict[str, Any]] = []
    for host in requested_hosts:
        binding = get_module_binding(store.get("bindings", {}), host, module_key)
        if not isinstance(binding, dict) or not binding.get("policy_id"):
            results.append({"host": host, "status": "failed", "message": "节点未绑定策略"})
            continue
        response = apply_policy(
            config,
            {
                "policy_id": binding["policy_id"],
                "hosts": [host],
                "sync": False,
            },
        )
        results.extend(response.get("results", []))

    sync_result = {"rc": 0, "stdout": "", "stderr": "sync skipped"}
    if is_truthy(payload.get("sync", True)):
        sync_result = run_policy_sync(config, mode=str(payload.get("sync_mode") or "control-loop"))

    return {
        "ok": all(item.get("status") == "success" for item in results) and sync_result.get("rc") == 0,
        "results": results,
        "sync": sync_result,
    }


class TrustMonitorHandler(SimpleHTTPRequestHandler):
    config: dict[str, Any] = {}
    status_cache: StatusCache | None = None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/api/status":
                force = query.get("force", ["0"])[0] in ("1", "true", "yes")
                if self.status_cache:
                    self.write_json(self.status_cache.get(force=force))
                else:
                    self.write_json(collect_status(self.config))
                return
            if parsed.path == "/api/policies":
                status = self.status_cache.get() if self.status_cache else None
                self.write_json(collect_policy_overview(self.config, status=status))
                return
            if parsed.path == "/api/policies/baseline":
                self.write_json(collect_tpm_baseline_summary(self.config))
                return
            if parsed.path == "/api/health":
                self.write_json({"ok": True})
                return
            super().do_GET()
        except ApiError as exc:
            self.write_json({"ok": False, "error": exc.message}, status=exc.status)
        except Exception as exc:  # pragma: no cover - defensive API boundary
            self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            self.require_admin()
            payload = self.read_json_body()
            if parsed.path == "/api/policies":
                self.write_json(upsert_policy(self.config, payload))
                return
            if parsed.path == "/api/policies/apply":
                self.write_json(apply_policy(self.config, payload))
                return
            if parsed.path == "/api/policies/import-baseline":
                self.write_json(import_baseline_policies(self.config, payload))
                return
            if parsed.path == "/api/policies/apply-bound":
                self.write_json(apply_bound_policies(self.config, payload))
                return
            raise ApiError(HTTPStatus.NOT_FOUND, f"Unknown API path: {parsed.path}")
        except ApiError as exc:
            self.write_json({"ok": False, "error": exc.message}, status=exc.status)
        except Exception as exc:  # pragma: no cover - defensive API boundary
            self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        try:
            self.require_admin()
            prefix = "/api/policies/"
            if not parsed.path.startswith(prefix):
                raise ApiError(HTTPStatus.NOT_FOUND, f"Unknown API path: {parsed.path}")
            policy_id = unquote(parsed.path[len(prefix):]).strip()
            if not policy_id:
                raise ApiError(HTTPStatus.BAD_REQUEST, "policy id 不能为空")
            self.write_json(delete_policy(self.config, policy_id))
        except ApiError as exc:
            self.write_json({"ok": False, "error": exc.message}, status=exc.status)
        except Exception as exc:  # pragma: no cover - defensive API boundary
            self.write_json({"ok": False, "error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def require_admin(self) -> None:
        token = str(self.config.get("KEYLIME_POLICY_ADMIN_TOKEN", "")).strip()
        if not token:
            return
        provided = self.headers.get("X-Admin-Token", "")
        if provided != token:
            raise ApiError(HTTPStatus.UNAUTHORIZED, "缺少或错误的 X-Admin-Token")

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求体不能为空")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"JSON 无效: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "请求体必须是 JSON 对象")
        return parsed

    def write_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the Keylime OpenStack trust console.")
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
    TrustMonitorHandler.status_cache = StatusCache(TrustMonitorHandler.config)
    TrustMonitorHandler.status_cache.start()
    server = ThreadingHTTPServer((args.host, args.port), TrustMonitorHandler)
    print(f"Serving Keylime OpenStack console on http://{args.host}:{args.port}/")
    print("Status API: /api/status")
    print("TPM PCR policy API: /api/policies")
    print(f"Background status refresh: every {TrustMonitorHandler.status_cache.interval:g}s")
    server.serve_forever()


if __name__ == "__main__":
    main()
