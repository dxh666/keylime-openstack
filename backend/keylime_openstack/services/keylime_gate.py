"""Keylime-only attestation gate shared by CLI and API."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from keylime_openstack.constants import (
    CAPABILITY_EVM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    PROVIDER_OPENTCSM,
    TRUST_AGENT_KEYLIME,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_AGENT_UNMANAGED,
    REGISTRATION_UNTRUSTED,
    REGISTRATION_VERIFIED,
)
from keylime_openstack.config import get_settings
from keylime_openstack.database import SessionLocal
from keylime_openstack.models import ComputeNode, EvidenceRecord
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.sync import keylime_status_to_evidence, latest_evidence_for_decision
from keylime_openstack.services.trust_agents import (
    node_trust_agent_name,
    node_trust_agent_type,
    node_trust_managed,
    node_trusted_root,
    node_trusted_root_type,
)
from keylime_openstack.services.trust_registration import (
    ensure_trusted_node_profile,
    profile_payload,
)
from keylime_openstack.services.trust_capabilities import build_trust_capability_summary


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
            .options(joinedload(ComputeNode.trust_profile))
            .where(ComputeNode.role == "compute")
            .where(ComputeNode.enabled.is_(True))
            .order_by(ComputeNode.hostname)
        )
        nodes = list(session.scalars(query).all())
        for node in nodes:
            ensure_trusted_node_profile(session, node, settings)
        session.flush()
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
                item = _keylime_node_check(session, client, node, settings)
                evaluated_count += 1
            elif agent_type == TRUST_AGENT_UNMANAGED and include_non_keylime:
                item = _unmanaged_node_check(node, settings)
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
    session,
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
        "trust_managed": True,
        "trusted_root_type": node_trusted_root_type(node, settings),
        "trusted_root": node_trusted_root(node, settings),
        "trust_capabilities": keylime_capabilities,
        **_profile_fields(node),
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
    now = datetime.now(timezone.utc)
    for record in records:
        record.collected_at = record.collected_at or now
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
    profile = getattr(node, "trust_profile", None)
    trust_capability_summary = (
        build_trust_capability_summary(session, node, profile, records)
        if profile
        else {}
    )
    capability_status = {
        "boot": _summary_capability_pass(
            trust_capability_summary,
            CAPABILITY_TRUSTED_BOOT,
            boot_ok and boot_fresh,
        ),
        "ima": runtime_ok and runtime_fresh,
        "evm": evm_ok and evm_fresh,
    }
    enabled_capabilities = [
        name for name, enabled in keylime_capabilities.items() if enabled
    ]
    trusted = bool(enabled_capabilities) and all(
        capability_status[name] for name in enabled_capabilities
    )
    _sync_keylime_profile(
        node,
        trusted=trusted,
        evidence=evidence,
        capability_status=capability_status,
        trust_capability_summary=trust_capability_summary,
        reason=_keylime_only_reason(
            evidence,
            evidence_fresh,
            keylime_capabilities,
            trust_capability_summary,
        ),
        reported_ip=reported_ip,
    )
    active_event_id = _active_last_event_id(status)
    reason = (
        "TRUSTED"
        if trusted
        else _keylime_only_reason(
            evidence,
            evidence_fresh,
            keylime_capabilities,
            trust_capability_summary,
        )
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
        "trust_capability_summary": trust_capability_summary,
        "capability_status": capability_status,
        "remediation": _remediation(
            event_id=str(active_event_id or reason),
            trusted=trusted,
            host=node.hostname,
            trust_agent_type=TRUST_AGENT_KEYLIME,
        ),
    }


def _sync_keylime_profile(
    node: ComputeNode,
    *,
    trusted: bool,
    evidence: dict[str, str],
    capability_status: dict[str, bool],
    trust_capability_summary: dict[str, dict[str, object]],
    reason: str,
    reported_ip: str,
) -> None:
    profile = getattr(node, "trust_profile", None)
    if not profile:
        return
    identity = dict(profile.agent_identity or {})
    identity["keylime_agent_uuid"] = node.keylime_agent_uuid
    endpoint = dict(profile.agent_endpoint or {})
    endpoint["host"] = reported_ip or node.keylime_agent_ip or node.management_ip
    endpoint["port"] = node.keylime_agent_port
    profile.agent_identity = identity
    profile.agent_endpoint = endpoint
    profile.last_verified_at = datetime.now(timezone.utc)
    profile.last_evidence_summary = {
        "trusted": trusted,
        "evidence": evidence,
        "capability_status": capability_status,
        "trust_capabilities": trust_capability_summary,
        "boot_measurement_summary": {
            "type": "tpm_measured_boot",
            "status": evidence.get("boot") or "unknown",
        },
        "reason": "TRUSTED" if trusted else reason,
        "source": "keylime",
    }
    profile.registration_status = REGISTRATION_VERIFIED if trusted else REGISTRATION_UNTRUSTED


def _external_trust_agent_node_check(
    session,
    node: ComputeNode,
    settings,
) -> dict[str, object]:
    agent_type = node_trust_agent_type(node, settings)
    capabilities = _profile_check_capabilities(node, settings)
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
    profile = getattr(node, "trust_profile", None)
    trust_capability_summary = (
        build_trust_capability_summary(session, node, profile, records)
        if profile
        else {}
    )
    boot_ok = evidence.get("boot") == "pass" and evidence_fresh.get("boot", False)
    runtime_ok = evidence.get("runtime") == "pass" and evidence_fresh.get("runtime", False)
    evm_ok = evidence.get("evm") == "pass" and evidence_fresh.get("evm", False)
    capability_status = {
        "boot": _summary_capability_pass(
            trust_capability_summary,
            CAPABILITY_TRUSTED_BOOT,
            boot_ok,
        ),
        "ima": _summary_capability_pass(
            trust_capability_summary,
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
            runtime_ok,
        ),
        "evm": evm_ok,
    }
    enabled_capabilities = [name for name, enabled in capabilities.items() if enabled]
    trusted = bool(enabled_capabilities) and all(
        capability_status[name] for name in enabled_capabilities
    )
    reason = (
        "TRUSTED"
        if trusted
        else _external_agent_reason(
            evidence,
            evidence_fresh,
            capabilities,
            agent_type,
            trust_capability_summary,
        )
    )
    latest_record = max(records, key=lambda item: item.collected_at, default=None)
    report_record = max(
        (record for record in records if record.provider == PROVIDER_OPENTCSM),
        key=lambda item: item.collected_at,
        default=latest_record,
    )
    _sync_external_profile(
        node,
        trusted=trusted,
        evidence=evidence,
        capability_status=capability_status,
        trust_capability_summary=trust_capability_summary,
        reason=reason,
        report_record=report_record,
    )
    latest_record_epoch = _external_record_epoch(latest_record)
    return {
        "host": node.hostname,
        "agent_uuid": "",
        "agent_ip": node.management_ip,
        "trust_agent_type": agent_type,
        "trust_agent_name": node_trust_agent_name(node, settings),
        "trust_managed": node_trust_managed(node, settings),
        "trusted_root_type": node_trusted_root_type(node, settings),
        "trusted_root": node_trusted_root(node, settings),
        **_profile_fields(node),
        "trusted": trusted,
        "reason": reason,
        "status": "collected" if records else "pending",
        "source": agent_type,
        "attestation_status": "PASS" if trusted else "UNKNOWN",
        "operational_state": "external",
        "last_event_id": None if trusted else reason,
        "has_runtime_policy": bool(records),
        "last_received_quote": latest_record_epoch,
        "last_successful_attestation": latest_record_epoch if trusted else None,
        "attestation_age_seconds": _external_evidence_age_seconds(latest_record),
        "tpm_policy": {},
        "evidence": evidence,
        "evidence_fresh": evidence_fresh,
        "evidence_valid_until": evidence_valid_until,
        "trust_report": _external_report_summary(report_record),
        "trust_report_history": _external_report_history(session, node),
        "trust_capabilities": capabilities,
        "trust_capability_summary": trust_capability_summary,
        "capability_status": capability_status,
        "remediation": _remediation(
            event_id=reason,
            trusted=trusted,
            host=node.hostname,
            trust_agent_type=agent_type,
        ),
    }


def _sync_external_profile(
    node: ComputeNode,
    *,
    trusted: bool,
    evidence: dict[str, str],
    capability_status: dict[str, bool],
    trust_capability_summary: dict[str, dict[str, object]],
    reason: str,
    report_record,
) -> None:
    profile = getattr(node, "trust_profile", None)
    if not profile:
        return
    report = _external_report_summary(report_record)
    identity = dict(profile.agent_identity or {})
    if report.get("tpcm_id"):
        identity["tpcm_id"] = report["tpcm_id"]
    profile.agent_identity = identity
    profile.last_verified_at = datetime.now(timezone.utc)
    profile.last_evidence_summary = {
        "trusted": trusted,
        "evidence": evidence,
        "capability_status": capability_status,
        "trust_capabilities": trust_capability_summary,
        "boot_measurement_summary": report.get("boot_measurement_summary") or {},
        "dynamic_measurement_summary": {
            "type": "tpcm_dynamic_measurement",
            "enabled": report.get("dynamic_measure_on"),
            "status": evidence.get("runtime") or "unknown",
            "object_count": len(report.get("dmeasure_policy") or []),
            "dmeasure_times": report.get("dmeasure_times"),
            "policy_sha256": report.get("dmeasure_policy_sha256") or "",
        },
        "license_summary": report.get("license") or {},
        "reason": "TRUSTED" if trusted else reason,
        "source": report.get("provider") or "external",
    }
    profile.registration_status = REGISTRATION_VERIFIED if trusted else REGISTRATION_UNTRUSTED


def _unmanaged_node_check(node: ComputeNode, settings) -> dict[str, object]:
    reason = "TRUST_AGENT_UNMANAGED"
    return {
        "host": node.hostname,
        "agent_uuid": "",
        "agent_ip": node.management_ip,
        "trust_agent_type": TRUST_AGENT_UNMANAGED,
        "trust_agent_name": node_trust_agent_name(node, settings),
        "trust_managed": False,
        "trusted_root_type": node_trusted_root_type(node, settings),
        "trusted_root": node_trusted_root(node, settings),
        **_profile_fields(node),
        "trusted": False,
        "reason": reason,
        "status": "unmanaged",
        "source": TRUST_AGENT_UNMANAGED,
        "attestation_status": "UNMANAGED",
        "operational_state": "unmanaged",
        "last_event_id": reason,
        "has_runtime_policy": False,
        "last_received_quote": None,
        "last_successful_attestation": None,
        "attestation_age_seconds": None,
        "tpm_policy": {},
        "evidence": {},
        "evidence_fresh": {},
        "evidence_valid_until": {},
        "trust_capabilities": {},
        "capability_status": {},
        "remediation": _remediation(
            event_id=reason,
            trusted=False,
            host=node.hostname,
            trust_agent_type=TRUST_AGENT_UNMANAGED,
        ),
    }


def _profile_fields(node: ComputeNode) -> dict[str, object]:
    profile = getattr(node, "trust_profile", None)
    if not profile:
        return {
            "openstack_compute_name": node.hypervisor_name or node.hostname,
            "adapter_type": "",
            "agent_endpoint": {},
            "agent_identity": {},
            "capabilities": {},
            "registration_status": "",
            "last_verified_at": None,
            "last_evidence_summary": {},
            "trusted_node_profile": None,
        }
    payload = profile_payload(profile, node)
    last_verified_at = payload.get("last_verified_at")
    if isinstance(last_verified_at, datetime):
        payload["last_verified_at"] = last_verified_at.isoformat()
    return {
        "openstack_compute_name": payload["openstack_compute_name"],
        "adapter_type": payload["adapter_type"],
        "agent_endpoint": payload["agent_endpoint"],
        "agent_identity": payload["agent_identity"],
        "capabilities": payload["capabilities"],
        "registration_status": payload["registration_status"],
        "last_verified_at": payload["last_verified_at"],
        "last_evidence_summary": payload["last_evidence_summary"],
        "trusted_node_profile": payload,
    }


def _external_report_history(
    session,
    node: ComputeNode,
    *,
    limit: int = 5,
) -> list[dict[str, object]]:
    records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.node_id == node.id)
        .where(EvidenceRecord.provider == PROVIDER_OPENTCSM)
        .order_by(EvidenceRecord.collected_at.desc(), EvidenceRecord.id.desc())
        .limit(limit * 4)
    ).all()
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for record in records:
        payload = record.payload or {}
        raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
        key = (
            record.collected_at.isoformat() if record.collected_at else "",
            str(raw.get("trust_report_sha256") or ""),
        )
        if key not in grouped:
            grouped[key] = _external_report_summary(record)
        if record.evidence_type == "boot":
            grouped[key]["boot_status"] = record.status
        if record.evidence_type == "runtime":
            grouped[key]["dynamic_measurement_status"] = record.status
        if record.evidence_type == "evm":
            grouped[key]["evm_status"] = record.status
    return list(grouped.values())[:limit]


def _external_report_summary(record) -> dict[str, object]:
    if not record:
        return {}
    payload = record.payload or {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    failures = raw.get("trust_report_failures") if isinstance(raw.get("trust_report_failures"), dict) else {}
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    boot_records = raw.get("boot_records") if isinstance(raw.get("boot_records"), list) else []
    dmeasure_policy = raw.get("dmeasure_policy") if isinstance(raw.get("dmeasure_policy"), list) else []
    reference_count = raw.get("boot_measure_ref_number")
    boot_measurement_summary = {
        "type": "tpcm_boot_measurement",
        "enabled": raw.get("boot_measure_on"),
        "record_count": len(boot_records),
        "reference_count": reference_count,
        "records_preview": boot_records[:5],
        "baseline_ready": bool(reference_count),
        "records_sha256": raw.get("boot_measure_records_sha256") or "",
        "trust_report_sha256": raw.get("trust_report_sha256") or "",
    }
    return {
        "provider": record.provider,
        "agent_name": payload.get("agent_name") or "",
        "trust_root": payload.get("trust_root") or "",
        "report_type": payload.get("report_type") or "",
        "collected_at": record.collected_at.isoformat() if record.collected_at else None,
        "valid_until": record.valid_until.isoformat() if record.valid_until else None,
        "status": record.status,
        "summary": record.summary,
        "trusted": payload.get("trusted"),
        "trust_status": raw.get("trust_status") or "unknown",
        "tpcm_id": raw.get("tpcm_id") or "",
        "boot_measure_on": raw.get("boot_measure_on"),
        "dynamic_measure_on": raw.get("dynamic_measure_on"),
        "trust_report_clean": raw.get("trust_report_clean"),
        "trust_report_eval": raw.get("trust_report_eval"),
        "trust_report_failures": failures,
        "failure_count": len(failures),
        "boot_record_count": len(boot_records),
        "dmeasure_times": raw.get("dmeasure_times"),
        "boot_measure_ref_number": raw.get("boot_measure_ref_number"),
        "dynamic_measure_ref_number": raw.get("dynamic_measure_ref_number"),
        "dmeasure_policy": dmeasure_policy,
        "license": raw.get("license") if isinstance(raw.get("license"), dict) else {},
        "global_control_policy": raw.get("global_control_policy")
        if isinstance(raw.get("global_control_policy"), dict)
        else {},
        "trust_report_sha256": raw.get("trust_report_sha256") or "",
        "dmeasure_policy_sha256": raw.get("dmeasure_policy_sha256") or "",
        "policy_report_sha256": raw.get("policy_report_sha256") or "",
        "global_control_policy_sha256": raw.get("global_control_policy_sha256") or "",
        "boot_measure_records_sha256": raw.get("boot_measure_records_sha256") or "",
        "boot_measurement_summary": boot_measurement_summary,
        "errors": errors,
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
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
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
    collected_at = record.collected_at
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - collected_at).total_seconds()))


def _external_record_epoch(record) -> int | None:
    if not record:
        return None
    collected_at = record.collected_at
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    return int(collected_at.timestamp())


def _keylime_only_reason(
    evidence: dict[str, str],
    evidence_fresh: dict[str, bool],
    capabilities: dict[str, bool],
    trust_capability_summary: dict[str, dict[str, object]] | None = None,
) -> str:
    missing = []
    checks = (
        ("BOOT", "boot", "boot", CAPABILITY_TRUSTED_BOOT),
        ("IMA", "ima", "runtime", CAPABILITY_IMA_RUNTIME),
        ("EVM", "evm", "evm", CAPABILITY_EVM),
    )
    for label, capability_name, evidence_type, product_capability in checks:
        if not capabilities.get(capability_name):
            continue
        capability_reason = (
            _summary_capability_reason(trust_capability_summary, product_capability)
            if product_capability == CAPABILITY_TRUSTED_BOOT
            else ""
        )
        if capability_reason:
            missing.append(f"{label}_{capability_reason}")
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
    trust_capability_summary: dict[str, dict[str, object]] | None = None,
) -> str:
    missing = []
    checks = (
        ("BOOT", "boot", "boot", CAPABILITY_TRUSTED_BOOT),
        ("TPCM_DYNAMIC_MEASUREMENT", "ima", "runtime", CAPABILITY_TPCM_DYNAMIC_MEASUREMENT),
        ("EVM", "evm", "evm", CAPABILITY_EVM),
    )
    for label, capability_name, evidence_type, product_capability in checks:
        if not capabilities.get(capability_name):
            continue
        capability_reason = (
            _summary_capability_reason(trust_capability_summary, product_capability)
            if product_capability in {
                CAPABILITY_TRUSTED_BOOT,
                CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
            }
            else ""
        )
        if capability_reason:
            missing.append(f"{label}_{capability_reason}")
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


def _summary_capability_pass(
    trust_capability_summary: dict[str, dict[str, object]],
    capability: str,
    fallback: bool,
) -> bool:
    item = trust_capability_summary.get(capability)
    if not item:
        return fallback
    return item.get("effective") is True or str(item.get("status") or "").lower() == "pass"


def _summary_capability_reason(
    trust_capability_summary: dict[str, dict[str, object]] | None,
    capability: str,
) -> str:
    item = (trust_capability_summary or {}).get(capability)
    if not item:
        return ""
    status = str(item.get("status") or "").strip().upper()
    if status in {
        "UNCONFIGURED",
        "DISABLED",
        "STALE",
        "FAIL",
        "UNKNOWN",
        "MISSING",
        "QUEUED",
        "APPLYING",
        "AWAITING_REBOOT",
        "EXTERNAL_PENDING",
        "NOT_DEPLOYED",
        "SUPERSEDED",
    }:
        return status
    return ""


def _profile_check_capabilities(node: ComputeNode, settings) -> dict[str, bool]:
    global_capabilities = {
        name: enabled
        for name, enabled in settings.effective_trust_capabilities.items()
        if name in {"boot", "ima", "evm"}
    }
    profile = getattr(node, "trust_profile", None)
    product_capabilities = dict(getattr(profile, "capabilities", {}) or {})
    if not product_capabilities:
        return global_capabilities
    return {
        "boot": bool(
            global_capabilities.get("boot")
            and product_capabilities.get(CAPABILITY_TRUSTED_BOOT) is True
        ),
        "ima": bool(
            global_capabilities.get("ima")
            and (
                product_capabilities.get(CAPABILITY_IMA_RUNTIME) is True
                or product_capabilities.get(CAPABILITY_TPCM_DYNAMIC_MEASUREMENT) is True
            )
        ),
        "evm": bool(
            global_capabilities.get("evm")
            and product_capabilities.get(CAPABILITY_EVM) is True
        ),
    }


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
        return {"category": "none", "summary": "无需处理。"}

    event = event_id.lower()
    if trust_agent_type == TRUST_AGENT_UNMANAGED:
        return {
            "category": "inventory",
            "summary": "计算节点未纳入可信代理纳管。",
            "next_commands": [
                "确认该 OpenStack 计算节点是否需要纳管；如需要，安装并配置 TPM/TPCM 可信代理后执行可信节点纳管同步。",
                "curl -fsS -X POST http://127.0.0.1:8088/api/trust/registrations/sync -H \"X-Admin-Token: $ADMIN_TOKEN\"",
            ],
        }
    if trust_agent_type == TRUST_AGENT_OPENTCSM_TPCM:
        if "stale" in event:
            return {
                "category": "opentcsm-tpcm",
                "summary": "OpenTCSM/TPCM 可信报告已过期。",
                "next_commands": [
                    f"进入节点管理页面，刷新 {host} 的可信状态。",
                    f"cd /opt/keylime-openstack && bash deploy/scripts/opentcsm-evidence-collect.sh {host}",
                ],
            }
        return {
            "category": "opentcsm-tpcm",
            "summary": "等待 OpenTCSM/Hygon TPCM 可信报告。",
            "next_commands": [
                f"进入节点管理页面，刷新 {host} 的可信状态。",
                f"cd /opt/keylime-openstack && bash deploy/scripts/opentcsm-evidence-collect.sh {host}",
            ],
        }

    if event == "missing-agent-uuid":
        return {
            "category": "inventory",
            "summary": "可信平面节点清单中缺少该节点的 TPM 代理 UUID。",
            "next_commands": [
                "curl -fsS -X POST http://127.0.0.1:8088/api/trust/registrations/sync -H \"X-Admin-Token: $ADMIN_TOKEN\"",
                "deploy/scripts/keylime-agent-inventory-refresh.sh",
            ],
        }
    if event == "keylime-verifier-agent-not-found":
        return {
            "category": "verifier-enrollment",
            "summary": "TPM 可信验证器中没有该节点的有效纳管记录。",
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
            "summary": "Verifier API 读取失败，且 API 容器内无法使用 Docker 执行 tenant-tool 兜底命令。",
            "next_commands": [
                "在 csri10 的 /opt/keylime-docker 目录执行 Keylime tenant 命令。",
                "仅在需要 tenant-tool 兜底时，再为 API 容器配置 Docker 访问能力。",
            ],
        }
    if event == "keylime-api-error":
        return {
            "category": "keylime-api",
            "summary": "可信平面无法读取 TPM 可信验证器状态。",
            "next_commands": [
                "docker ps | grep -E 'keylime-verifier|keylime-registrar'",
                "docker compose logs --tail=120 keylime-verifier",
            ],
        }
    if event.startswith("internal.verifier.not_reachable"):
        return {
            "category": "agent-reachability",
            "summary": "TPM 可信验证器无法访问该节点 TPM 代理；检查代理容器和网络后重新激活。",
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
            "summary": "IMA 运行时度量与已绑定策略不一致。",
            "next_commands": [
                f"deploy/scripts/keylime-only-attestation-repair.sh --hosts {host}",
                f"deploy/scripts/keylime-ima-runtime-policy-diff.sh {host} bound",
                f"deploy/scripts/keylime-ima-runtime-policy-refresh.sh {host}",
            ],
        }
    if event.startswith(("pcr.", "measured_boot.", "quote.", "tpm.")):
        return {
            "category": "boot-tpm-policy",
            "summary": "TPM/PCR 可信启动证据未通过；先处理可信启动策略或启动证据。",
            "next_commands": [
                "deploy/scripts/keylime-tpm-evidence-audit.sh",
            ],
        }
    if "stale" in event:
        return {
            "category": "freshness",
            "summary": "可信证明已过期；等待 verifier 刷新，或检查代理与 verifier 的证明循环。",
            "next_commands": [
                f"deploy/scripts/keylime-only-attestation-check.sh --strict --hosts {host}",
            ],
        }
    if "boot_" in event:
        return {
            "category": "boot-tpm-policy",
            "summary": "可信启动证据未通过；先检查 PCR 策略和启动事件日志，再处理运行时策略。",
            "next_commands": [
                "deploy/scripts/keylime-tpm-evidence-audit.sh",
            ],
        }
    if "ima_missing" in event or "runtime_missing" in event:
        return {
            "category": "ima-runtime-policy",
            "summary": "TPM 可信验证器中未绑定该节点的 IMA 运行时策略；当前先保留，后续再处理策略下发与重启流程。",
            "next_commands": [
                "curl -fsS http://127.0.0.1:8088/api/tasks?limit=10",
                "进入 IMA 运行时策略页面，确认策略绑定状态为已下发；如未下发，重新下发该节点策略。",
                f"deploy/scripts/keylime-only-attestation-check.sh --strict --hosts {host}",
            ],
        }
    if "ima_" in event or "runtime_" in event:
        return {
            "category": "ima-runtime-policy",
            "summary": "IMA 运行时证据未通过；比对实时度量与已绑定策略。",
            "next_commands": [
                f"deploy/scripts/keylime-ima-runtime-policy-diff.sh {host} bound",
            ],
        }
    return {
        "category": "unknown",
        "summary": "检查 TPM 可信验证器日志和该节点的可信证据。",
        "next_commands": [
            "docker compose logs --since=20m keylime-verifier",
        ],
    }
