"""Product-level trust capability summaries for compute nodes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.constants import (
    BINDING_NODE,
    CAPABILITY_EVM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    POLICY_DEPLOY_APPLIED,
    POLICY_IMA_RUNTIME,
    POLICY_MEASURED_BOOT,
    POLICY_TPCM_DYNAMIC_MEASUREMENT,
    TRUST_ROOT_TPCM,
)
from keylime_openstack.models import (
    AuditEvent,
    ComputeNode,
    EvidenceRecord,
    PolicyBinding,
    TrustPolicy,
    TrustedNodeProfile,
)

CAPABILITY_POLICY_TYPES = {
    CAPABILITY_TRUSTED_BOOT: POLICY_MEASURED_BOOT,
    CAPABILITY_IMA_RUNTIME: POLICY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: POLICY_TPCM_DYNAMIC_MEASUREMENT,
}

CAPABILITY_EVIDENCE_TYPES = {
    CAPABILITY_TRUSTED_BOOT: "boot",
    CAPABILITY_IMA_RUNTIME: "runtime",
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: "runtime",
    CAPABILITY_EVM: "evm",
}


def build_trust_capability_summary(
    session: Session,
    node: ComputeNode,
    profile: TrustedNodeProfile,
    evidence_records: list[EvidenceRecord] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a stable product summary for each trust capability."""

    evidence_records = evidence_records or []
    latest = {record.evidence_type: record for record in evidence_records}
    capabilities = dict(profile.capabilities or {})
    dynamic_global_enabled = _tpcm_dynamic_global_enabled(session)
    return {
        capability: _capability_item(
            session,
            node,
            profile,
            capability,
            supported=capabilities.get(capability) is True,
            evidence=latest.get(CAPABILITY_EVIDENCE_TYPES[capability]),
            dynamic_global_enabled=dynamic_global_enabled,
        )
        for capability in (
            CAPABILITY_TRUSTED_BOOT,
            CAPABILITY_IMA_RUNTIME,
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
            CAPABILITY_EVM,
        )
    }


def _capability_item(
    session: Session,
    node: ComputeNode,
    profile: TrustedNodeProfile,
    capability: str,
    *,
    supported: bool,
    evidence: EvidenceRecord | None,
    dynamic_global_enabled: bool,
) -> dict[str, Any]:
    binding = _active_capability_binding(session, node, profile, capability)
    policy_bound = bool(binding and binding.application_status == POLICY_DEPLOY_APPLIED)
    policy_enabled = _policy_enabled(
        binding,
        capability,
        dynamic_global_enabled=dynamic_global_enabled,
    )
    evidence_status = str(evidence.status) if evidence else "missing"
    evidence_fresh = _evidence_fresh(evidence)
    status = _capability_status(
        supported,
        policy_bound,
        policy_enabled,
        evidence_status,
        evidence_fresh,
        binding,
    )
    reason = _capability_reason(
        supported=supported,
        policy_bound=policy_bound,
        policy_enabled=policy_enabled,
        evidence_status=evidence_status,
        evidence_fresh=evidence_fresh,
        binding=binding,
        evidence=evidence,
    )
    return {
        "supported": supported,
        "enabled": supported,
        "policy_bound": policy_bound,
        "policy_enabled": policy_enabled,
        "policy_status": binding.application_status if binding else "not_deployed",
        "status": status,
        "effective": status == "pass",
        "evidence_type": CAPABILITY_EVIDENCE_TYPES[capability],
        "provider": evidence.provider if evidence else "",
        "last_verified_at": evidence.collected_at.isoformat() if evidence else None,
        "valid_until": evidence.valid_until.isoformat() if evidence and evidence.valid_until else None,
        "evidence_fresh": evidence_fresh,
        "reason": reason,
        "binding_id": binding.id if binding else None,
        "application_status": binding.application_status if binding else "not_deployed",
        "baseline": _baseline_summary(binding, capability),
    }


def _active_capability_binding(
    session: Session,
    node: ComputeNode,
    profile: TrustedNodeProfile,
    capability: str,
) -> PolicyBinding | None:
    policy_type = CAPABILITY_POLICY_TYPES.get(capability)
    if not policy_type:
        return None
    statement = (
        select(PolicyBinding)
        .join(TrustPolicy, TrustPolicy.id == PolicyBinding.policy_id)
        .where(PolicyBinding.target_type == BINDING_NODE)
        .where(PolicyBinding.target_id == node.id)
        .where(PolicyBinding.active.is_(True))
        .where(TrustPolicy.policy_type == policy_type)
        .order_by(PolicyBinding.id.desc())
    )
    pending: PolicyBinding | None = None
    for binding in session.scalars(statement).all():
        if not _binding_matches_capability(binding, profile, capability):
            continue
        if binding.application_status == POLICY_DEPLOY_APPLIED:
            return binding
        if pending is None:
            pending = binding
    return pending


def _binding_matches_capability(
    binding: PolicyBinding,
    profile: TrustedNodeProfile,
    capability: str,
) -> bool:
    content = dict(binding.policy.content or {})
    if capability != CAPABILITY_TRUSTED_BOOT:
        return True
    root = str(content.get("trusted_root_type") or "tpm").lower()
    if profile.trusted_root_type == TRUST_ROOT_TPCM:
        return root == TRUST_ROOT_TPCM
    return root != TRUST_ROOT_TPCM


def _policy_enabled(
    binding: PolicyBinding | None,
    capability: str,
    *,
    dynamic_global_enabled: bool,
) -> bool:
    if not binding or binding.application_status != POLICY_DEPLOY_APPLIED:
        return False
    if capability != CAPABILITY_TPCM_DYNAMIC_MEASUREMENT:
        return True
    if not dynamic_global_enabled:
        return False
    content = dict(binding.policy.content or {})
    return bool(
        content.get(
            "node_dynamic_measure_enabled",
            content.get("dynamic_measure_required", True),
        )
    )


def _evidence_fresh(evidence: EvidenceRecord | None) -> bool:
    if not evidence:
        return False
    if evidence.valid_until is None:
        return True
    valid_until = evidence.valid_until
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    return valid_until >= datetime.now(timezone.utc)


def _capability_status(
    supported: bool,
    policy_bound: bool,
    policy_enabled: bool,
    evidence_status: str,
    evidence_fresh: bool,
    binding: PolicyBinding | None,
) -> str:
    if not supported:
        return "unsupported"
    if not policy_bound:
        if binding and binding.application_status == "failed":
            return "fail"
        return "unconfigured"
    if not policy_enabled:
        return "disabled"
    if evidence_status == "pass" and not evidence_fresh:
        return "stale"
    return evidence_status or "missing"


def _capability_reason(
    *,
    supported: bool,
    policy_bound: bool,
    policy_enabled: bool,
    evidence_status: str,
    evidence_fresh: bool,
    binding: PolicyBinding | None,
    evidence: EvidenceRecord | None,
) -> str:
    if not supported:
        return "capability unsupported by this trusted root"
    if binding and binding.application_status != POLICY_DEPLOY_APPLIED:
        return binding.last_error or f"policy deployment {binding.application_status}"
    if not policy_bound:
        return "policy is not applied"
    if not policy_enabled:
        return "policy is disabled"
    if evidence_status == "pass" and not evidence_fresh:
        return "capability evidence is stale"
    if evidence_status == "pass":
        return evidence.summary if evidence else "capability evidence passed"
    if evidence:
        return evidence.summary
    return "capability evidence is missing"


def _baseline_summary(binding: PolicyBinding | None, capability: str) -> dict[str, Any]:
    if not binding:
        return {}
    details = dict(binding.binding_details or {})
    rendered = dict(binding.rendered_policy or {})
    if capability == CAPABILITY_TRUSTED_BOOT:
        expected = dict(rendered.get("expected") or {})
        return {
            "artifact": details.get("keylime_artifact") or details.get("adapter_artifact") or "",
            "status": details.get("baseline_status") or "",
            "content_sha256": details.get("rendered_policy_sha256") or "",
            "evidence_sha256": details.get("evidence_sha256") or "",
            "boot_record_count": (
                expected.get("boot_record_count")
                if expected
                else details.get("boot_record_count")
            ),
            "boot_reference_count": (
                expected.get("boot_reference_count")
                if expected
                else details.get("boot_measure_ref_number")
            ),
            "tpcm_write_status": details.get("tpcm_write_status") or "",
        }
    return {
        "artifact": details.get("keylime_artifact") or "",
        "status": details.get("policy_apply_status") or details.get("baseline_status") or "",
        "content_sha256": details.get("rendered_policy_sha256") or "",
        "evidence_sha256": details.get("evidence_sha256") or "",
    }


def _tpcm_dynamic_global_enabled(session: Session) -> bool:
    event = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == "tpcm_dynamic_global_switch")
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).first()
    if not event:
        return True
    details = dict(event.event_details or {})
    return details.get("global_dynamic_measure_enabled") is not False
