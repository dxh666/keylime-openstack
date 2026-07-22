"""Policy deployment binding state persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.constants import (
    POLICY_DEPLOY_APPLIED,
    POLICY_DEPLOY_FAILED,
    POLICY_DEPLOY_SUPERSEDED,
    POLICY_MEASURED_BOOT,
)
from keylime_openstack.models import PolicyBinding, TrustPolicy
from keylime_openstack.services.policy import canonical_policy_type

__all__ = ["PolicyDeploymentState"]


class PolicyDeploymentState:
    def __init__(self, session: Session) -> None:
        self.session = session

    def mark_applied(
        self,
        binding: PolicyBinding,
        policy: TrustPolicy,
        external_name: str,
        rendered_policy: dict[str, Any],
        deployment_details: dict[str, Any] | None = None,
    ) -> None:
        binding.application_status = POLICY_DEPLOY_APPLIED
        binding.external_policy_name = external_name
        binding.rendered_policy = rendered_policy
        binding.applied_at = datetime.now(timezone.utc)
        binding.last_error = ""
        binding.binding_details = {
            **dict(binding.binding_details or {}),
            **(deployment_details or {}),
            "external_policy_name": external_name,
            "policy_type": canonical_policy_type(policy.policy_type),
        }
        other_bindings = self.session.scalars(
            select(PolicyBinding)
            .join(TrustPolicy, TrustPolicy.id == PolicyBinding.policy_id)
            .where(PolicyBinding.target_type == binding.target_type)
            .where(PolicyBinding.target_id == binding.target_id)
            .where(PolicyBinding.id != binding.id)
            .where(PolicyBinding.active.is_(True))
            .where(TrustPolicy.policy_type == policy.policy_type)
        ).all()
        for old_binding in other_bindings:
            old_binding.active = False
            old_binding.application_status = POLICY_DEPLOY_SUPERSEDED

    def active_external_policy_name(self, node_id: int, policy_type: str) -> str:
        binding = self.session.scalars(
            select(PolicyBinding)
            .join(TrustPolicy, TrustPolicy.id == PolicyBinding.policy_id)
            .where(PolicyBinding.target_type == "node")
            .where(PolicyBinding.target_id == node_id)
            .where(PolicyBinding.active.is_(True))
            .where(PolicyBinding.application_status == POLICY_DEPLOY_APPLIED)
            .where(TrustPolicy.policy_type == policy_type)
            .order_by(PolicyBinding.applied_at.desc())
            .limit(1)
        ).first()
        if binding and policy_type == POLICY_MEASURED_BOOT:
            details = dict(binding.binding_details or {})
            if details.get("keylime_artifact") != "measured_boot_refstate":
                return ""
        return binding.external_policy_name if binding else ""

    def active_boot_policy_adapter(self, node_id: int) -> dict[str, Any]:
        binding = self.session.scalars(
            select(PolicyBinding)
            .join(TrustPolicy, TrustPolicy.id == PolicyBinding.policy_id)
            .where(PolicyBinding.target_type == "node")
            .where(PolicyBinding.target_id == node_id)
            .where(PolicyBinding.active.is_(True))
            .where(PolicyBinding.application_status == POLICY_DEPLOY_APPLIED)
            .where(TrustPolicy.policy_type == POLICY_MEASURED_BOOT)
            .order_by(PolicyBinding.applied_at.desc())
            .limit(1)
        ).first()
        if not binding:
            return {}
        details = dict(binding.binding_details or {})
        if details.get("keylime_artifact") == "measured_boot_refstate":
            return {"measured_boot_policy_name": binding.external_policy_name}
        if details.get("keylime_artifact") == "tpm_pcr_quote_policy":
            return {
                "tpm_policy": dict(binding.rendered_policy or {}),
                "disable_measured_boot": True,
            }
        return {}

    @staticmethod
    def mark_failed(
        binding: PolicyBinding,
        error: str,
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        display_error = str(
            (extra_details or {}).get("policy_apply_error_summary")
            or (extra_details or {}).get("policy_last_result_summary")
            or error
        )
        binding.application_status = POLICY_DEPLOY_FAILED
        binding.last_error = display_error[:4000]
        details = {
            **dict(binding.binding_details or {}),
            "policy_apply_status": POLICY_DEPLOY_FAILED,
            "policy_apply_error_summary": display_error[:500],
            "policy_last_result": "failed",
            "policy_last_result_summary": display_error[:500],
            "policy_last_result_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra_details:
            details.update(extra_details)
        binding.binding_details = details

    @staticmethod
    def binding_result(binding: PolicyBinding, status: str) -> dict[str, Any]:
        return {
            "binding_id": binding.id,
            "target_id": binding.target_id,
            "status": status,
            "external_policy_name": binding.external_policy_name,
            "error": binding.last_error,
        }
