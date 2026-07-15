"""Keylime-only attestation gate shared by CLI and API."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from sqlalchemy import select

from keylime_openstack.config import get_settings
from keylime_openstack.database import SessionLocal
from keylime_openstack.models import ComputeNode
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.sync import keylime_status_to_evidence


def keylime_only_check(
    hosts: str = "",
    *,
    count: int = 1,
    interval_seconds: int = 30,
    failures_only: bool = False,
) -> dict[str, object]:
    count = max(1, count)
    interval_seconds = max(0, interval_seconds)
    runs = []
    for run_index in range(count):
        result = _keylime_only_check_once(hosts=hosts, failures_only=failures_only)
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
        "openstack_enforcement_enabled": latest["openstack_enforcement_enabled"],
        "latest_nodes_total": latest["nodes_total"],
        "latest_nodes_trusted": latest["nodes_trusted"],
        "runs": runs,
    }


def _keylime_only_check_once(
    *,
    hosts: str = "",
    failures_only: bool = False,
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
        all_ok = bool(nodes)
        trusted_count = 0
        for node in nodes:
            item = _keylime_node_check(client, node, settings)
            all_ok = all_ok and bool(item.get("trusted"))
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
        "openstack_enforcement_enabled": settings.openstack_enforcement_enabled,
        "nodes_total": len(nodes),
        "nodes_trusted": trusted_count,
        "nodes_returned": len(results),
        "nodes": results,
    }


def _keylime_node_check(
    client: KeylimeClient,
    node: ComputeNode,
    settings,
) -> dict[str, object]:
    base: dict[str, object] = {"host": node.hostname, "agent_uuid": node.keylime_agent_uuid}
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
            ),
        }

    try:
        status = client.read_agent_status(node.keylime_agent_uuid)
    except Exception as exc:
        return {
            **base,
            "agent_ip": node.keylime_agent_ip,
            "trusted": False,
            "status": "error",
            "reason": str(exc),
            "remediation": _remediation(
                event_id="keylime-api-error",
                trusted=False,
                host=node.hostname,
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
    boot_fresh = evidence_fresh.get("boot", False)
    runtime_fresh = evidence_fresh.get("runtime", False)
    trusted = boot_ok and runtime_ok and boot_fresh and runtime_fresh
    active_event_id = _active_last_event_id(status)
    reason = "TRUSTED" if trusted else _keylime_only_reason(evidence, evidence_fresh)
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
        "remediation": _remediation(
            event_id=str(active_event_id or reason),
            trusted=trusted,
            host=node.hostname,
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


def _keylime_only_reason(
    evidence: dict[str, str],
    evidence_fresh: dict[str, bool],
) -> str:
    missing = []
    for label, evidence_type in (("BOOT", "boot"), ("IMA", "runtime")):
        status = evidence.get(evidence_type, "missing")
        fresh = evidence_fresh.get(evidence_type, False)
        if status == "pass" and not fresh:
            missing.append(f"{label}_STALE")
        elif status != "pass":
            missing.append(f"{label}_{status.upper()}")
    if not missing:
        return "NOT_TRUSTED"
    return "WAITING_FOR_" + "_".join(missing)


def _remediation(*, event_id: str, trusted: bool, host: str) -> dict[str, object]:
    if trusted:
        return {"category": "none", "summary": "No action required."}

    event = event_id.lower()
    if event == "missing-agent-uuid":
        return {
            "category": "inventory",
            "summary": "Node has no Keylime agent UUID in the trust-plane inventory.",
            "next_commands": [
                "deploy/scripts/keylime-agent-inventory-refresh.sh",
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
