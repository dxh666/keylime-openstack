"""Audit recording for policy deployment workflows."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.constants import (
    POLICY_DEPLOY_APPLIED,
    POLICY_MEASURED_BOOT,
    POLICY_TPCM_DYNAMIC_MEASUREMENT,
)
from keylime_openstack.models import PolicyBinding
from keylime_openstack.services.audit import record_audit_event
from keylime_openstack.services.tpcm_dynamic_policies import _dynamic_audit_object_name

__all__ = ["PolicyDeploymentAuditRecorder"]


class PolicyDeploymentAuditRecorder:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_policy_deployment(
        self,
        *,
        policy_name: str,
        policy_type: str,
        hostname: str,
        binding: PolicyBinding,
        details: dict[str, Any],
    ) -> None:
        is_dynamic = policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT
        is_tpcm_boot = (
            policy_type == POLICY_MEASURED_BOOT
            and details.get("keylime_artifact") == "opentcsm_tpcm_boot_policy"
        )
        event_details = {
            "binding_id": binding.id,
            "executor": binding.executor,
            **details,
        }
        if is_tpcm_boot:
            baseline = (
                details.get("rendered_policy_sha256")
                or details.get("boot_measure_records_sha256")
                or details.get("evidence_sha256")
                or ""
            )
            event_details = {
                **event_details,
                "log_type": "trusted_boot",
                "policy_type": policy_type,
                "subject_name": "TPCM",
                "object_name": hostname,
                "measurement_type": "策略生效",
                "measurement_baseline": baseline,
                "operation": "管理侧基线绑定",
                "result": "成功"
                if binding.application_status == POLICY_DEPLOY_APPLIED
                else "失败",
                "hash": baseline,
            }
        if is_dynamic:
            baseline = (
                details.get("dmeasure_policy_sha256")
                or details.get("policy_sha256")
                or details.get("rendered_policy_sha256")
                or ""
            )
            auth_code = str(details.get("policy_apply_error_code") or "")
            is_auth_event = auth_code.startswith("TPCM_AUTH_")
            event_details = {
                **event_details,
                "log_type": "tpcm_authorization" if is_auth_event else "dynamic_measurement",
                "policy_type": policy_type,
                "subject_name": "TPCM",
                "object_name": _dynamic_audit_object_name(details),
                "measurement_type": "授权检测" if is_auth_event else "策略生效",
                "measurement_baseline": baseline,
                "operation": "授权检测" if is_auth_event else "策略生效",
                "result": "成功"
                if binding.application_status == POLICY_DEPLOY_APPLIED
                else "失败",
                "hash": baseline,
            }
        message = f"policy deployment {binding.application_status}"
        if is_dynamic and binding.application_status != POLICY_DEPLOY_APPLIED:
            message = str(details.get("policy_apply_error_summary") or message)
        elif is_tpcm_boot and binding.application_status != POLICY_DEPLOY_APPLIED:
            message = str(
                details.get("policy_apply_error_summary")
                or details.get("error")
                or "trusted boot policy baseline failed"
            )
        elif is_tpcm_boot:
            message = "trusted boot policy baseline applied"

        record_audit_event(
            self.session,
            event_type=(
                "tpcm_dynamic_policy_apply"
                if is_dynamic
                else "trusted_boot_policy_apply"
                if is_tpcm_boot
                else "policy_deploy"
            ),
            target=f"{policy_name}:{hostname}",
            severity=(
                "info"
                if binding.application_status == POLICY_DEPLOY_APPLIED
                else "warning"
            ),
            message=message,
            event_details=event_details,
        )
