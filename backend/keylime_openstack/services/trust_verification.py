"""Unified trusted-node verification orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    ADAPTER_KEYLIME,
    ADAPTER_OPENTCSM,
    CAPABILITY_EVM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    REGISTRATION_CONFLICT,
    REGISTRATION_REGISTERED,
    REGISTRATION_UNMANAGED,
    REGISTRATION_UNTRUSTED,
    REGISTRATION_VERIFIED,
    TRUST_ROOT_TPCM,
)
from keylime_openstack.models import ComputeNode, TrustedNodeProfile
from keylime_openstack.services.opentcsm_collect import OpenTcsmCollector
from keylime_openstack.services.sync_collectors import (
    TrustEvidenceCollector,
    latest_evidence_for_decision,
)
from keylime_openstack.services.trust_registration import (
    ensure_trusted_node_profile,
    profile_payload,
)
from keylime_openstack.services.trust_capabilities import build_trust_capability_summary

__all__ = ["verify_trusted_node"]


def verify_trusted_node(
    session: Session,
    settings: Settings,
    node: ComputeNode,
) -> dict[str, Any]:
    profile = ensure_trusted_node_profile(session, node, settings)
    if not profile.trust_managed:
        result: dict[str, Any] = {
            "ok": False,
            "status": REGISTRATION_UNMANAGED,
            "reason": "trusted-agent-unmanaged",
            "trusted": False,
            "evidence": {},
        }
    elif profile.adapter_type == ADAPTER_KEYLIME:
        result = TrustEvidenceCollector(session, settings).collect_tpm_evidence(node)
    elif profile.adapter_type == ADAPTER_OPENTCSM:
        result = OpenTcsmCollector(session, settings).collect(node)
    else:
        result = {
            "ok": False,
            "status": REGISTRATION_CONFLICT,
            "reason": "unknown-trust-agent-adapter",
            "trusted": False,
            "evidence": {},
        }

    session.flush()
    summary = _evidence_summary(session, node, profile, result)
    result["trusted"] = summary["trusted"]
    profile.last_verified_at = datetime.now(timezone.utc)
    profile.last_evidence_summary = summary
    profile.registration_status = _registration_status(profile, result, summary)
    session.flush()
    return {
        "ok": result.get("ok") is not False and profile.registration_status != REGISTRATION_CONFLICT,
        "node": node.hostname,
        "openstack_compute_name": profile.openstack_compute_name,
        "status": result.get("status") or profile.registration_status,
        "trusted": summary["trusted"],
        "trusted_node_profile": profile_payload(profile, node),
        "verification": result,
        "evidence_summary": summary,
    }


def _registration_status(
    profile: TrustedNodeProfile,
    result: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    status = str(result.get("status") or "")
    if not profile.trust_managed:
        return REGISTRATION_UNMANAGED
    if status == REGISTRATION_CONFLICT:
        return REGISTRATION_CONFLICT
    if summary["trusted"] is True:
        return REGISTRATION_VERIFIED
    if status in {"collected", "error", "skipped"} or summary.get("evidence"):
        return REGISTRATION_UNTRUSTED
    return REGISTRATION_REGISTERED


def _evidence_summary(
    session: Session,
    node: ComputeNode,
    profile: TrustedNodeProfile,
    result: dict[str, Any],
) -> dict[str, Any]:
    records = latest_evidence_for_decision(session, node)
    evidence = {record.evidence_type: record.status for record in records}
    providers = {record.evidence_type: record.provider for record in records}
    valid_until = {
        record.evidence_type: record.valid_until.isoformat() if record.valid_until else None
        for record in records
    }
    raw = _result_raw(result)
    trusted = _capability_trusted(profile, evidence, result)
    return {
        "trusted": trusted,
        "evidence": evidence,
        "providers": providers,
        "valid_until": valid_until,
        "capability_status": _capability_status(profile, evidence),
        "trust_capabilities": build_trust_capability_summary(
            session,
            node,
            profile,
            records,
        ),
        "boot_measurement_summary": _boot_measurement_summary(profile, evidence, raw),
        "dynamic_measurement_summary": _dynamic_measurement_summary(profile, evidence, raw),
        "reason": result.get("reason") or result.get("summary") or "",
        "source": result.get("provider") or result.get("source") or profile.adapter_type or "",
    }


def _capability_trusted(
    profile: TrustedNodeProfile,
    evidence: dict[str, str],
    result: dict[str, Any],
) -> bool:
    if isinstance(result.get("trusted"), bool):
        return bool(result["trusted"])
    status = _capability_status(profile, evidence)
    enabled = [value for value in status.values() if value != "disabled"]
    return bool(enabled) and all(value == "pass" for value in enabled)


def _capability_status(profile: TrustedNodeProfile, evidence: dict[str, str]) -> dict[str, str]:
    capabilities = dict(profile.capabilities or {})
    return {
        CAPABILITY_TRUSTED_BOOT: _enabled_status(capabilities, CAPABILITY_TRUSTED_BOOT, evidence, "boot"),
        CAPABILITY_IMA_RUNTIME: _enabled_status(capabilities, CAPABILITY_IMA_RUNTIME, evidence, "runtime"),
        CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: _enabled_status(
            capabilities,
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
            evidence,
            "runtime",
        ),
        CAPABILITY_EVM: _enabled_status(capabilities, CAPABILITY_EVM, evidence, "evm"),
    }


def _enabled_status(
    capabilities: dict[str, Any],
    capability: str,
    evidence: dict[str, str],
    evidence_type: str,
) -> str:
    if capabilities.get(capability) is not True:
        return "disabled"
    return evidence.get(evidence_type) or "missing"


def _boot_measurement_summary(
    profile: TrustedNodeProfile,
    evidence: dict[str, str],
    raw: dict[str, Any],
) -> dict[str, Any]:
    if profile.trusted_root_type == TRUST_ROOT_TPCM:
        boot_records = raw.get("boot_records") if isinstance(raw.get("boot_records"), list) else []
        boot_references = (
            raw.get("boot_references") if isinstance(raw.get("boot_references"), list) else []
        )
        reference_count = raw.get("boot_measure_ref_number")
        return {
            "type": "tpcm_boot_measurement",
            "enabled": raw.get("boot_measure_on"),
            "status": evidence.get("boot") or "unknown",
            "record_count": len(boot_records),
            "reference_count": reference_count,
            "records_preview": boot_records[:5],
            "references_preview": boot_references[:5],
            "baseline_ready": bool(reference_count),
            "records_sha256": raw.get("boot_measure_records_sha256") or "",
            "references_sha256": raw.get("boot_measure_references_sha256") or "",
            "trust_report_sha256": raw.get("trust_report_sha256") or "",
        }
    return {
        "type": "tpm_measured_boot",
        "status": evidence.get("boot") or "unknown",
    }


def _dynamic_measurement_summary(
    profile: TrustedNodeProfile,
    evidence: dict[str, str],
    raw: dict[str, Any],
) -> dict[str, Any]:
    if profile.trusted_root_type != TRUST_ROOT_TPCM:
        return {}
    policy = raw.get("dmeasure_policy") if isinstance(raw.get("dmeasure_policy"), list) else []
    return {
        "type": "tpcm_dynamic_measurement",
        "enabled": raw.get("dynamic_measure_on"),
        "status": evidence.get("runtime") or "unknown",
        "object_count": len(policy),
        "dmeasure_times": raw.get("dmeasure_times"),
        "policy_sha256": raw.get("dmeasure_policy_sha256") or "",
    }


def _result_raw(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("raw")
    return raw if isinstance(raw, dict) else {}
