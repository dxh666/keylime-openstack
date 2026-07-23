"""Product-level trust capability summaries for compute nodes."""

from __future__ import annotations

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
from keylime_openstack.models import ComputeNode, EvidenceRecord, PolicyBinding, TrustPolicy, TrustedNodeProfile

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
    return {
        capability: _capability_item(
            session,
            node,
            profile,
            capability,
            supported=capabilities.get(capability) is True,
            evidence=latest.get(CAPABILITY_EVIDENCE_TYPES[capability]),
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
) -> dict[str, Any]:
    binding = _active_capability_binding(session, node, profile, capability)
    policy_bound = bool(binding and binding.application_status == POLICY_DEPLOY_APPLIED)
    evidence_status = str(evidence.status) if evidence else "missing"
    status = _capability_status(supported, policy_bound, evidence_status)
    reason = _capability_reason(
        supported=supported,
        policy_bound=policy_bound,
        evidence_status=evidence_status,
        binding=binding,
        evidence=evidence,
    )
    return {
        "supported": supported,
        "enabled": supported,
        "policy_bound": policy_bound,
        "status": status,
        "evidence_type": CAPABILITY_EVIDENCE_TYPES[capability],
        "provider": evidence.provider if evidence else "",
        "last_verified_at": evidence.collected_at.isoformat() if evidence else None,
        "valid_until": evidence.valid_until.isoformat() if evidence and evidence.valid_until else None,
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
        .order_by(PolicyBinding.applied_at.desc(), PolicyBinding.id.desc())
        .limit(5)
    )
    for binding in session.scalars(statement).all():
        content = dict(binding.policy.content or {})
        if capability == CAPABILITY_TRUSTED_BOOT:
            root = str(content.get("trusted_root_type") or "tpm").lower()
            if profile.trusted_root_type == TRUST_ROOT_TPCM:
                if root == TRUST_ROOT_TPCM:
                    return binding
                continue
            if root != TRUST_ROOT_TPCM:
                return binding
            continue
        return binding
    return None


def _capability_status(supported: bool, policy_bound: bool, evidence_status: str) -> str:
    if not supported:
        return "unsupported"
    if not policy_bound:
        return "unconfigured" if evidence_status in {"missing", ""} else evidence_status
    return evidence_status or "missing"


def _capability_reason(
    *,
    supported: bool,
    policy_bound: bool,
    evidence_status: str,
    binding: PolicyBinding | None,
    evidence: EvidenceRecord | None,
) -> str:
    if not supported:
        return "capability unsupported by this trusted root"
    if binding and binding.application_status != POLICY_DEPLOY_APPLIED:
        return binding.last_error or f"policy deployment {binding.application_status}"
    if not policy_bound:
        return "policy is not bound"
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
