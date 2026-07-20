"""Keylime-only attestation gate shared by CLI and API."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from sqlalchemy import select

from keylime_openstack.constants import TRUST_AGENT_KEYLIME, TRUST_AGENT_OPENTCSM_TPCM
from keylime_openstack.config import get_settings
from keylime_openstack.database import SessionLocal
from keylime_openstack.models import ComputeNode
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.sync import keylime_status_to_evidence, latest_evidence_for_decision
from keylime_openstack.services.trust_agents import (
    node_trust_agent_name,
    node_trust_agent_type,
    node_trusted_root,
)


def keylime_only_check(
    hosts: str = "",
    *,
    count: int = 1,
    interval_seconds: int = 30,
    failures_only: bool = False,
    include_non_keylime: bool = False,
) -> dict[str, object]:
    count = max(1, count)
    interval_seconds = max(0, interval_seconds)
    runs = []
    for run_index in range(count):
        result = _keylime_only_check_once(
            hosts=hosts,
            failures_only=failures_only,
            include_non_keylime=include_non_keylime,
        )
        result["run"] = run_index + 1
        runs.append(result)
        if run_index + 1 < count:
            time.sleep(interval_seconds)

    if count == 1:
        return runs[0]

    latest = runs[-1]
    return {
        "ok": all(bool(run.get("ok")) for run in runs),
        "mode": "keylime-only-stability",
        "count": count,
        "interval_seconds": interval_seconds,
        "trust_policy_mode": latest["trust_policy_mode"],
        "trust_capabilities": latest["trust_capabilities"],
        "openstack_enforcement_enabled": latest["openstack_enforcement_enabled"],
        "latest_nodes_total": latest["nodes_total"],
        "latest_nodes_trusted": latest["nodes_trusted"],
        "runs": runs,
    }


def _keylime_only_check_once(
    *,
    hosts: str = "",
    failures_only: bool = False,
    include_non_keylime: bool = False,
) -> dict[str, object]:
    settings = get_settings()
    wanted = {item.strip() for item in hosts.split(",") if item.strip()}
    with SessionLocal() as session:
        ensure_default_environment(session)
        session.flush()
        query = (
            select(ComputeNode)
            .where(ComputeNode.role == "compute")
            .where(ComputeNode.enabled.is_(True))
            .order_by(ComputeNode.hostname)
        )
        nodes = list(session.scalars(query).all())
        if wanted:
            nodes = [node for node in nodes if node.hostname in wanted]

        client = KeylimeClient(settings)
        results = []
        all_ok = False
        trusted_count = 0
        evaluated_count = 0
        skipped_count = 0
        for node in nodes:
            agent_type = node_trust_agent_type(node, settings)
            if agent_type == TRUST_AGENT_KEYLIME:
                item = _keylime_node_check(client, node, settings)
                evaluated_count += 1
            elif include_non_keylime:
                item = _external_trust_agent_node_check(session, node, settings)
                evaluated_count += 1
            else:
                skipped_count += 1
                continue
            all_ok = bool(item.get("trusted")) if evaluated_count == 1 else all_ok and bool(item.get("trusted"))
            if item.get("trusted"):
                trusted_count += 1
            if not failures_only or not item.get("trusted"):
                results.append(item)
        session.commit()

    return {
        "ok": all_ok,
        "mode": "keylime-only",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "trust_policy_mode": settings.normalized_trust_policy_mode,
        "trust_capabilities": settings.effective_trust_capabilities,
        "openstack_enforcement_enabled": settings.openstack_enforcement_enabled,
        "nodes_total": evaluated_count,
        "nodes_trusted": trusted_count,
        "nodes_returned": len(results),
        "nodes_skipped": skipped_count,
        "nodes": results,
    }


def _keylime_node_check(
    client: KeylimeClient,
    node: ComputeNode,
    settings,
) -> dict[str, object]:
    keylime_capabilities = {
        name: enabled
        for name, enabled in settings.effective_trust_capabilities.items()
        if name in {"boot", "ima", "evm"}
    }
    base: dict[str, object] = {
        "host": node.hostname,
        "agent_uuid": node.keylime_agent_uuid,
        "trust_agent_type": TRUST_AGENT_KEYLIME,
        "trust_agent_name": node_trust_agent_name(node, settings),
        "trusted_root": node_trusted_root(node, settings),
        "trust_capabilities": keylime_capabilities,
    }
    if not node.keylime_agent_uuid:
        return {
            **base,
            "agent_ip": node.keylime_agent_ip,
            "trusted": False,
            "status": "skipped",
            "reason": "missing-agent-uuid",
            "remediation": _remediation(
                event_id="missing-agent-uuid",
                trusted=False,
                host=node.hostname,
                trust_agent_type=TRUST_AGENT_KEYLIME,
            ),
        }

    try:
        status = client.read_agent_status(node.keylime_agent_uuid)
    except Exception as exc:
        event_id = _keylime_read_error_event(str(exc))
        return {
            **base,
            "agent_ip": node.keylime_agent_ip,
            "trusted": False,
            "status": "error",
            "reason": str(exc),
            "remediation": _remediation(
                event_id=event_id,
                trusted=False,
                host=node.hostname,
                trust_agent_type=TRUST_AGENT_KEYLIME,
            ),
        }

    reported_ip = str(status.get("ip") or node.keylime_agent_ip or "")
    if reported_ip and node.keylime_agent_ip != reported_ip:
        node.keylime_agent_ip = reported_ip
    records = keylime_status_to_evidence(node, status, settings)
    evidence = {record.evidence_type: record.status for record in records}
    evidence_fresh = {
        record.evidence_type: _record_fresh(record.valid_until)
        for record in records
    }
    evidence_valid_until = {
        record.evidence_type: record.valid_until.isoformat() if record.valid_until else None
        for record in records
    }
    boot_ok = evidence.get("boot") == "pass"
    runtime_ok = evidence.get("runtime") == "pass"
    evm_ok = evidence.get("evm") == "pass"
    boot_fresh = evidence_fresh.get("boot", False)
    runtime_fresh = evidence_fresh.get("runtime", False)
    evm_fresh = evidence_fresh.get("evm", False)
    capability_status = {
        "boot": boot_ok and boot_fresh,
        "ima": runtime_ok and runtime_fresh,
        "evm": evm_ok and evm_fresh,
    }
    enabled_capabilities = [
        name for name, enabled in keylime_capabilities.items() if enabled
    ]
    trusted = bool(enabled_capabilities) and all(
        capability_status[name] for name in enabled_capabilities
    )
    active_event_id = _active_last_event_id(status)
    reason = (
        "TRUSTED"
        if trusted
        else _keylime_only_reason(evidence, evidence_fresh, keylime_capabilities)
    )
    return {
        **base,
        "agent_ip": reported_ip,
        "trusted": trusted,
        "reason": reason,
        "status": "collected",
        "source": status.get("_source") or "unknown",
        "attestation_status": status.get("attestation_status"),
        "operational_state": status.get("operational_state"),
        "last_event_id": active_event_id,
        "has_runtime_policy": _truthy(status.get("has_runtime_policy")),
        "last_received_quote": status.get("last_received_quote"),
        "last_successful_attestation": status.get("last_successful_attestation"),
        "attestation_age_seconds": _attestation_age_seconds(status),
        "tpm_policy": _json_or_value(status.get("tpm_policy")),
        "evidence": evidence,
        "evidence_fresh": evidence_fresh,
        "evidence_valid_until": evidence_valid_until,
        "trust_capabilities": keylime_capabilities,
        "capability_status": capability_status,
        "remediation": _remediation(
            event_id=str(active_event_id or reason),
            trusted=trusted,
            host=node.hostname,
            trust_agent_type=TRUST_AGENT_KEYLIME,
        ),
    }


def _external_trust_agent_node_check(
    session,
    node: ComputeNode,
    settings,
) -> dict[str, object]:
    agent_type = node_trust_agent_type(node, settings)
    capabilities = {
        name: enabled
        for name, enabled in settings.effective_trust_capabilities.items()
        if name in {"boot", "ima", "evm"}
    }
    records = latest_evidence_for_decision(session, node)
    evidence = {record.evidence_type: record.status for record in records}
    evidence_fresh = {
        record.evidence_type: _record_fresh(record.valid_until)
        for record in records
    }
    evidence_valid_until = {
        record.evidence_type: record.valid_until.isoformat() if record.valid_until else None
        for record in records
    }
    boot_ok = evidence.get("boot") == "pass" and evidence_fresh.get("boot", False)
    runtime_ok = evidence.get("runtime") == "pass" and evidence_fresh.get("runtime", False)
    evm_ok = evidence.get("evm") == "pass" and evidence_fresh.get("evm", False)
    capability_status = {
        "boot": boot_ok,
        "ima": runtime_ok,
        "evm": evm_ok,
    }
    enabled_capabilities = [name for name, enabled in capabilities.items() if enabled]
    trusted = bool(enabled_capabilities) and all(
        capability_status[name] for name in enabled_capabilities
    )
    reason = (
        "TRUSTED"
        if trusted
        else _external_agent_reason(evidence, evidence_fresh, capabilities, agent_type)
    )
    latest_record = max(records, key=lambda item: item.collected_at, default=None)
    return {
        "host": node.hostname,
        "agent_uuid": "",
        "agent_ip": node.management_ip,
        "trust_agent_type": agent_type,
        "trust_agent_name": node_trust_agent_name(node, settings),
        "trusted_root": node_trusted_root(node, settings),
        "trusted": trusted,
        "reason": reason,
        "status": "collected" if records else "pending",
        "source": agent_type,
        "attestation_status": "PASS" if trusted else "UNKNOWN",
        "operational_state": "external",
        "last_event_id": None if trusted else reason,
        "has_runtime_policy": bool(records),
        "last_received_quote": None,
        "last_successful_attestation": None,
        "attestation_age_seconds": _external_evidence_age_seconds(latest_record),
        "tpm_policy": {},
        "evidence": evidence,
        "evidence_fresh": evidence_fresh,
        "evidence_valid_until": evidence_valid_until,
        "trust_capabilities": capabilities,
        "capability_status": capability_status,
        "remediation": _remediation(
            event_id=reason,
            trusted=trusted,
            host=node.hostname,
            trust_agent_type=agent_type,
        ),
    }


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "enabled", "pass"}


def _json_or_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _active_last_event_id(status: dict[str, object]) -> object:
    if str(status.get("attestation_status") or "").upper() == "PASS":
        return None
    return status.get("last_event_id")


def _record_fresh(valid_until: datetime | None) -> bool:
    if valid_until is None:
        return True
    return valid_until >= datetime.now(timezone.utc)


def _attestation_age_seconds(status: dict[str, object]) -> int | None:
    timestamp = _coerce_epoch(status.get("last_successful_attestation"))
    if timestamp is None:
        timestamp = _coerce_epoch(status.get("last_received_quote"))
    if timestamp is None:
        return None
    return max(0, int(datetime.now(timezone.utc).timestamp()) - timestamp)


def _coerce_epoch(value: object) -> int | None:
    try:
        timestamp = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def _external_evidence_age_seconds(record) -> int | None:
    if not record:
        return None
    return max(0, int((datetime.now(timezone.utc) - record.collected_at).total_seconds()))


def _keylime_only_reason(
    evidence: dict[str, str],
    evidence_fresh: dict[str, bool],
    capabilities: dict[str, bool],
) -> str:
    missing = []
    checks = (
        ("BOOT", "boot", "boot"),
        ("IMA", "ima", "runtime"),
        ("EVM", "evm", "evm"),
    )
    for label, capability_name, evidence_type in checks:
        if not capabilities.get(capability_name):
            continue
        status = evidence.get(evidence_type, "missing")
        fresh = evidence_fresh.get(evidence_type, False)
        if status == "pass" and not fresh:
            missing.append(f"{label}_STALE")
        elif status != "pass":
            missing.append(f"{label}_{status.upper()}")
    if not missing:
        return "NO_KEYLIME_TRUST_CAPABILITY_ENABLED"
    return "WAITING_FOR_" + "_".join(missing)


def _external_agent_reason(
    evidence: dict[str, str],
    evidence_fresh: dict[str, bool],
    capabilities: dict[str, bool],
    agent_type: str,
) -> str:
    missing = []
    checks = (
        ("BOOT", "boot", "boot"),
        ("DYNAMIC", "ima", "runtime"),
        ("EVM", "evm", "evm"),
    )
    for label, capability_name, evidence_type in checks:
        if not capabilities.get(capability_name):
            continue
        status = evidence.get(evidence_type, "missing")
        fresh = evidence_fresh.get(evidence_type, False)
        if status == "pass" and not fresh:
            missing.append(f"{label}_STALE")
        elif status != "pass":
            missing.append(f"{label}_{status.upper()}")
    if not missing:
        return f"WAITING_FOR_{agent_type.upper()}_EVIDENCE"
    return "WAITING_FOR_" + "_".join(missing)


def _keylime_read_error_event(error: str) -> str:
    normalized = error.lower()
    if (
        "404 not found" in normalized
        or "agent not found" in normalized
        or "verifier status not found" in normalized
    ):
        return "keylime-verifier-agent-not-found"
    if "no such file or directory: 'docker'" in normalized:
        return "keylime-tenant-tool-unavailable"
    return "keylime-api-error"


def _remediation(
    *,
    event_id: str,
    trusted: bool,
    host: str,
    trust_agent_type: str,
) -> dict[str, object]:
    if trusted:
        return {"category": "none", "summary": "No action required."}

    event = event_id.lower()
    if trust_agent_type == TRUST_AGENT_OPENTCSM_TPCM:
        if "stale" in event:
            return {
                "category": "opentcsm-tpcm",
                "summary": "OpenTCSM/TPCM evidence is no longer fresh.",
                "next_commands": [
                    f"POST /api/nodes/{host}/opentcsm-evidence with a fresh TPCM report",
                ],
            }
        return {
            "category": "opentcsm-tpcm",
            "summary": "Waiting for OpenTCSM/Hygon TPCM evidence for this node.",
            "next_commands": [
                f"POST /api/nodes/{host}/opentcsm-evidence with boot and dynamic measurement status",
            ],
        }

    if event == "missing-agent-uuid":
        return {
            "category": "inventory",
            "summary": "Node has no Keylime agent UUID in the trust-plane inventory.",
            "next_commands": [
                "deploy/scripts/keylime-agent-inventory-refresh.sh",
            ],
        }
    if event == "keylime-verifier-agent-not-found":
        return {
            "category": "verifier-enrollment",
            "summary": "Keylime 验证器中没有该节点的有效纳管记录。",
            "next_commands": [
                "cd /opt/keylime-docker && docker compose run --rm keylime-tenant -c reglist",
                "cd /opt/keylime-docker && docker compose run --rm keylime-tenant -c cvlist",
                "进入可信启动策略页面，对该节点重新下发当前生效策略。",
                f"cd /opt/keylime-openstack && deploy/scripts/keylime-only-attestation-check.sh --strict --hosts {host}",
            ],
        }
    if event == "keylime-tenant-tool-unavailable":
        return {
            "category": "tenant-tool",
            "summary": "Verifier API failed and tenant-tool fallback cannot run because Docker is unavailable in the API container.",
            "next_commands": [
                "Run the Keylime tenant command from /opt/keylime-docker on csri10.",
                "Mount Docker access into the API container only if tenant-tool fallback is required.",
            ],
        }
    if event == "keylime-api-error":
        return {
            "category": "keylime-api",
            "summary": "Trust plane could not read Keylime verifier status.",
            "next_commands": [
                "docker ps | grep -E 'keylime-verifier|keylime-registrar'",
                "docker compose logs --tail=120 keylime-verifier",
            ],
        }
    if event.startswith("internal.verifier.not_reachable"):
        return {
            "category": "agent-reachability",
            "summary": "Verifier cannot reach the Keylime agent; check agent container/network, then reactivate.",
            "next_commands": [
                "ssh root@<agent-ip> 'docker ps | grep keylime-agent || true'",
                f"deploy/scripts/keylime-only-attestation-check.sh --strict --hosts {host}",
            ],
        }
    if event.startswith("ima.validation.ima-ng.runtime_policy_hash") or event.startswith(
        "ima.validation.ima-ng.not_in_allowlist"
    ):
        return {
            "category": "ima-runtime-policy",
            "summary": "IMA runtime policy does not match the live measurement list.",
            "next_commands": [
                f"deploy/scripts/keylime-only-attestation-repair.sh --hosts {host}",
                f"deploy/scripts/keylime-ima-runtime-policy-diff.sh {host} bound",
                f"deploy/scripts/keylime-ima-runtime-policy-refresh.sh {host}",
            ],
        }
    if event.startswith(("pcr.", "measured_boot.", "quote.", "tpm.")):
        return {
            "category": "boot-tpm-policy",
            "summary": "TPM/PCR boot evidence failed; do not refresh IMA runtime baseline first.",
            "next_commands": [
                "deploy/scripts/keylime-tpm-evidence-audit.sh",
            ],
        }
    if "stale" in event:
        return {
            "category": "freshness",
            "summary": "Attestation is no longer fresh; wait for verifier or check agent/verifier loop.",
            "next_commands": [
                f"deploy/scripts/keylime-only-attestation-check.sh --strict --hosts {host}",
            ],
        }
    if "boot_" in event:
        return {
            "category": "boot-tpm-policy",
            "summary": "Boot evidence is not trusted; inspect PCR policy and event log before runtime policy work.",
            "next_commands": [
                "deploy/scripts/keylime-tpm-evidence-audit.sh",
            ],
        }
    if "ima_" in event or "runtime_" in event:
        return {
            "category": "ima-runtime-policy",
            "summary": "IMA runtime evidence is not trusted; compare live measurements with the bound policy.",
            "next_commands": [
                f"deploy/scripts/keylime-ima-runtime-policy-diff.sh {host} bound",
            ],
        }
    return {
        "category": "unknown",
        "summary": "Inspect Keylime verifier logs and node-specific evidence.",
        "next_commands": [
            "docker compose logs --since=20m keylime-verifier",
        ],
    }
