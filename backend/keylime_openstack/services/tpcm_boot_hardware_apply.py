"""OpenTCSM/TPCM trusted boot hardware policy application."""

from __future__ import annotations

import json
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    ADAPTER_OPENTCSM,
    CAPABILITY_TRUSTED_BOOT,
    POLICY_DEPLOY_APPLIED,
    POLICY_MEASURED_BOOT,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_ROOT_TPCM,
)
from keylime_openstack.models import ComputeNode, PolicyBinding, TrustPolicy, TrustedNodeProfile
from keylime_openstack.services.ansible import AnsibleExecutor
from keylime_openstack.services.audit import record_audit_event
from keylime_openstack.services.opentcsm_collect import OpenTcsmCollector
from keylime_openstack.services.opentcsm_policy import load_opentcsm_auth_material
from keylime_openstack.services.policy import canonical_policy_type
from keylime_openstack.services.policy_artifacts import _content_hash
from keylime_openstack.services.trust_agents import node_trust_agent_type
from keylime_openstack.services.trust_registration import ensure_trusted_node_profile

__all__ = [
    "TpcmBootHardwareApplyError",
    "TpcmBootHardwareApplyService",
    "_opentcsm_boot_apply_error",
    "_opentcsm_boot_failure_details",
]


class TpcmBootHardwareApplyError(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class TpcmBootHardwareApplyService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.ansible = AnsibleExecutor(settings)

    def apply(
        self,
        policy: TrustPolicy,
        binding: PolicyBinding,
        node: ComputeNode,
    ) -> dict[str, Any]:
        profile = ensure_trusted_node_profile(self.session, node, self.settings)
        precheck_failures = _precheck(policy, binding, node, profile, self.settings)
        if precheck_failures:
            summary = "TPCM trusted boot hardware apply precheck failed: " + "; ".join(
                precheck_failures
            )
            details = _opentcsm_boot_failure_details(summary, status="precheck_failed")
            details.update({"precheck_failures": precheck_failures})
            self._mark_binding_result(binding, details)
            self._audit(policy, binding, node, details, success=False)
            raise TpcmBootHardwareApplyError(summary, details)

        content = dict(policy.content or {})
        auth_ref = str(
            content.get("auth_material_ref")
            or self.settings.opentcsm_default_boot_auth_ref
            or ""
        ).strip()
        try:
            auth = load_opentcsm_auth_material(self.settings, auth_ref)
        except RuntimeError as exc:
            details = _opentcsm_boot_failure_details(str(exc), status="authorization_failed")
            self._mark_binding_result(binding, details)
            self._audit(policy, binding, node, details, success=False)
            raise TpcmBootHardwareApplyError(details["tpcm_write_error_summary"], details) from exc

        started_at = datetime.now(timezone.utc).isoformat()
        self._mark_binding_result(
            binding,
            {
                "tpcm_write_status": "writing",
                "tpcm_write_enabled": True,
                "tpcm_auth_ref": auth.ref,
                "tpcm_auth_uid": auth.uid,
                "tpcm_auth_public_key_sha256": auth.public_fingerprint,
                "tpcm_write_last_result": "running",
                "tpcm_write_last_result_summary": "TPCM trusted boot hardware apply is running.",
                "tpcm_write_last_result_at": started_at,
            },
        )
        self.session.flush()

        rendered_policy = dict(binding.rendered_policy or {})
        with self._workspace() as workspace:
            apply_result_path = workspace / "opentcsm-boot-apply.json"
            apply_result = self.ansible.run(
                playbook="apply-opentcsm-boot-policy.yml",
                node=node,
                workspace=workspace,
                extra_vars={
                    "result_output_path": str(apply_result_path),
                    "opentcsm_boot_auth": auth.playbook_vars(),
                    "opentcsm_boot_policy": _boot_apply_policy(content, rendered_policy),
                },
            )
            opentcsm_apply = _load_apply_result(apply_result_path, apply_result.rc)
            if apply_result.rc != 0:
                if opentcsm_apply:
                    error = _opentcsm_boot_apply_error(opentcsm_apply)
                    details = _opentcsm_boot_failure_details(error, result=opentcsm_apply)
                    self._mark_binding_result(binding, details)
                    self._audit(policy, binding, node, details, success=False)
                    raise TpcmBootHardwareApplyError(details["tpcm_write_error_summary"], details)
                self._require_ansible_success(
                    apply_result.rc,
                    apply_result.stdout,
                    apply_result.stderr,
                )
            if not apply_result_path.is_file():
                raise TpcmBootHardwareApplyError(
                    "OpenTCSM boot hardware apply result was not produced",
                    _opentcsm_boot_failure_details(
                        "OpenTCSM boot hardware apply result was not produced"
                    ),
                )
            if opentcsm_apply is None:
                opentcsm_apply = _load_apply_result(apply_result_path, 0) or {}

        try:
            collect_result = self._collect_after_apply(node)
        except TpcmBootHardwareApplyError as exc:
            exc.details.update(
                {"tpcm_hardware_apply_result": _summarize_apply_result(opentcsm_apply)}
            )
            self._mark_binding_result(binding, exc.details)
            self._audit(policy, binding, node, exc.details, success=False)
            raise
        raw = collect_result.get("raw") if isinstance(collect_result.get("raw"), dict) else {}
        verify_failures = _post_apply_failures(content, raw)
        if verify_failures:
            summary = "TPCM trusted boot hardware verify failed: " + "; ".join(verify_failures)
            details = _opentcsm_boot_failure_details(summary, status="verify_failed")
            details.update(
                {
                    "verify_failures": verify_failures,
                    "tpcm_hardware_apply_result": _summarize_apply_result(opentcsm_apply),
                    "boot_measure_on": raw.get("boot_measure_on"),
                    "boot_measure_ref_number": raw.get("boot_measure_ref_number"),
                    "boot_measure_references_sha256": raw.get("boot_measure_references_sha256")
                    or "",
                }
            )
            self._mark_binding_result(binding, details)
            self._audit(policy, binding, node, details, success=False)
            raise TpcmBootHardwareApplyError(summary, details)

        success_details = {
            "tpcm_write_status": "enabled",
            "tpcm_write_enabled": True,
            "tpcm_control_status": "enabled",
            "tpcm_auth_ref": auth.ref,
            "tpcm_auth_uid": auth.uid,
            "tpcm_auth_public_key_sha256": auth.public_fingerprint,
            "tpcm_write_error_code": "",
            "tpcm_write_error_summary": "",
            "tpcm_write_last_result": "success",
            "tpcm_write_last_result_summary": (
                "TPCM trusted boot references were written and boot control was enabled."
            ),
            "tpcm_write_last_result_at": datetime.now(timezone.utc).isoformat(),
            "tpcm_hardware_apply_result": _summarize_apply_result(opentcsm_apply),
            "tpcm_hardware_apply_sha256": _content_hash(opentcsm_apply),
            "boot_measure_on": raw.get("boot_measure_on"),
            "boot_status": raw.get("boot_status") or "",
            "trust_status": raw.get("trust_status") or "",
            "boot_measure_ref_number": raw.get("boot_measure_ref_number"),
            "boot_measure_references_sha256": raw.get("boot_measure_references_sha256") or "",
            "boot_measure_records_sha256": raw.get("boot_measure_records_sha256") or "",
            "trust_report_sha256": raw.get("trust_report_sha256") or "",
            "policy_last_result": "success",
            "policy_last_result_summary": (
                "TPCM trusted boot references were written and boot control was enabled."
            ),
            "policy_last_result_at": datetime.now(timezone.utc).isoformat(),
        }
        self._mark_binding_result(binding, success_details)
        self._audit(policy, binding, node, success_details, success=True)
        self.session.flush()
        return {
            "ok": True,
            "policy_id": policy.id,
            "binding_id": binding.id,
            "node": node.hostname,
            "tpcm_write_status": "enabled",
            "boot_measure_on": raw.get("boot_measure_on"),
            "boot_measure_ref_number": raw.get("boot_measure_ref_number"),
            "boot_measure_references_sha256": raw.get("boot_measure_references_sha256") or "",
            "apply_result": _summarize_apply_result(opentcsm_apply),
        }

    def _collect_after_apply(self, node: ComputeNode) -> dict[str, Any]:
        try:
            return OpenTcsmCollector(self.session, self.settings).collect(node)
        except Exception as exc:
            details = _opentcsm_boot_failure_details(str(exc), status="verify_failed")
            raise TpcmBootHardwareApplyError(
                "TPCM trusted boot evidence refresh failed after hardware apply: " + str(exc),
                details,
            ) from exc

    def _mark_binding_result(self, binding: PolicyBinding, details: dict[str, Any]) -> None:
        binding.binding_details = {
            **dict(binding.binding_details or {}),
            **details,
        }

    def _audit(
        self,
        policy: TrustPolicy,
        binding: PolicyBinding,
        node: ComputeNode,
        details: dict[str, Any],
        *,
        success: bool,
    ) -> None:
        baseline = (
            details.get("boot_measure_references_sha256")
            or details.get("boot_measure_records_sha256")
            or details.get("rendered_policy_sha256")
            or dict(binding.binding_details or {}).get("rendered_policy_sha256")
            or ""
        )
        details["tpcm_boot_hardware_audit_recorded"] = True
        record_audit_event(
            self.session,
            event_type="tpcm_boot_hardware_apply",
            target=f"{policy.name}:{node.hostname}",
            severity="info" if success else "warning",
            message=(
                "TPCM trusted boot hardware apply succeeded"
                if success
                else "TPCM trusted boot hardware apply failed"
            ),
            event_details={
                "log_type": "trusted_boot",
                "policy_id": policy.id,
                "binding_id": binding.id,
                "subject_name": "TPCM",
                "object_name": node.hostname,
                "measurement_type": "hardware_apply",
                "measurement_baseline": baseline,
                "operation": "write_boot_references_and_enable_control",
                "result": "success" if success else "failed",
                "hash": baseline,
                **details,
            },
        )

    @contextmanager
    def _workspace(self):
        self.settings.temp_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            yield Path(tmp)

    @staticmethod
    def _require_ansible_success(rc: int, stdout: str, stderr: str) -> None:
        if rc == 0:
            return
        parts = [f"Ansible returned {rc}"]
        if stdout.strip():
            parts.append(f"stdout:\n{stdout.strip()}")
        if stderr.strip():
            parts.append(f"stderr:\n{stderr.strip()}")
        detail = "\n\n".join(parts)[-4000:]
        raise TpcmBootHardwareApplyError(
            detail,
            _opentcsm_boot_failure_details(detail),
        )


def _precheck(
    policy: TrustPolicy,
    binding: PolicyBinding,
    node: ComputeNode,
    profile: TrustedNodeProfile,
    settings: Settings,
) -> list[str]:
    content = dict(policy.content or {})
    details = dict(binding.binding_details or {})
    identity = dict(profile.agent_identity or {})
    capabilities = dict(profile.capabilities or {})
    failures: list[str] = []
    if canonical_policy_type(policy.policy_type) != POLICY_MEASURED_BOOT:
        failures.append("policy is not a trusted boot policy")
    if str(content.get("trusted_root_type") or "").lower() != TRUST_ROOT_TPCM:
        failures.append("policy trusted root is not TPCM")
    if details.get("keylime_artifact") != "opentcsm_tpcm_boot_policy":
        failures.append("policy baseline is not an OpenTCSM TPCM trusted boot baseline")
    if binding.application_status != POLICY_DEPLOY_APPLIED:
        failures.append("management-plane trusted boot baseline is not applied")
    if not profile.trust_managed:
        failures.append("node is not managed by a trusted agent")
    if profile.trusted_root_type != TRUST_ROOT_TPCM:
        failures.append("node trusted root is not TPCM")
    if profile.adapter_type != ADAPTER_OPENTCSM:
        failures.append("node trusted agent adapter is not OpenTCSM")
    if node_trust_agent_type(node, settings) != TRUST_AGENT_OPENTCSM_TPCM:
        failures.append("node trust agent type is not OpenTCSM/TPCM")
    if capabilities.get(CAPABILITY_TRUSTED_BOOT) is not True:
        failures.append("node does not support trusted boot capability")
    if not str(identity.get("tpcm_id") or "").strip():
        failures.append("node TPCM identity is missing")
    if not str(content.get("auth_material_ref") or settings.opentcsm_default_boot_auth_ref or ""):
        failures.append("TPCM boot authorization material reference is missing")
    rendered = dict(binding.rendered_policy or {})
    expected = rendered.get("expected") if isinstance(rendered.get("expected"), dict) else {}
    if not expected:
        failures.append("TPCM trusted boot baseline is missing")
    elif not expected.get("boot_records"):
        failures.append("TPCM trusted boot baseline has no boot records")
    return failures


def _boot_apply_policy(content: dict[str, Any], rendered_policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation": _operation_code(content.get("tpcm_operation") or "add"),
        "operation_name": str(content.get("tpcm_operation") or "add"),
        "stage": content.get("tpcm_stage"),
        "flag": content.get("tpcm_flag"),
        "enable_boot_control": content.get("tpcm_enable_boot_control") is not False,
        "expected": dict(rendered_policy.get("expected") or {}),
        "baseline_sha256": _content_hash(rendered_policy),
    }


def _operation_code(value: Any) -> str:
    normalized = str(value or "add").strip().lower()
    names = {
        "reset": "0",
        "0": "0",
        "add": "1",
        "1": "1",
        "delete": "2",
        "2": "2",
        "modify": "3",
        "3": "3",
    }
    if normalized not in names:
        raise TpcmBootHardwareApplyError(
            f"unsupported TPCM boot reference operation: {value}",
            _opentcsm_boot_failure_details(
                f"unsupported TPCM boot reference operation: {value}",
                status="precheck_failed",
            ),
        )
    return names[normalized]


def _load_apply_result(path: Path, rc: int) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        if rc == 0:
            raise TpcmBootHardwareApplyError(
                "OpenTCSM boot hardware apply result is not valid JSON",
                _opentcsm_boot_failure_details(
                    "OpenTCSM boot hardware apply result is not valid JSON"
                ),
            ) from exc
    return None


def _post_apply_failures(content: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    minimum_refs = _safe_int(content.get("minimum_boot_references")) or 0
    reference_count = _safe_int(raw.get("boot_measure_ref_number"))
    if bool(content.get("boot_measure_required", True)) and raw.get("boot_measure_on") is not True:
        failures.append("TPCM boot measurement control is not enabled")
    if reference_count is None or reference_count < minimum_refs:
        failures.append(
            f"TPCM boot reference count is below policy minimum ({reference_count or 0} < {minimum_refs})"
        )
    if str(raw.get("boot_status") or "").lower() != "pass":
        failures.append(f"TPCM boot status is {raw.get('boot_status') or 'unknown'}")
    return failures


def _opentcsm_boot_apply_error(result: dict[str, Any]) -> str:
    failed = [
        item
        for item in result.get("commands", [])
        if isinstance(item, dict) and int(item.get("rc") or 0) != 0
    ]
    if not failed:
        return str(result.get("error") or "OpenTCSM TPCM trusted boot hardware apply failed")
    details: list[str] = []
    for item in failed[:5]:
        command = item.get("command")
        command_text = " ".join(str(part) for part in command) if isinstance(command, list) else ""
        stdout = str(item.get("stdout") or "").strip()
        stderr = str(item.get("stderr") or "").strip()
        detail = f"{item.get('name') or 'command'} rc={item.get('rc')}"
        if command_text:
            detail += f"; command={command_text}"
        if stderr:
            detail += f"; stderr={stderr[-800:]}"
        if stdout:
            detail += f"; stdout={stdout[-800:]}"
        details.append(detail)
    return "OpenTCSM TPCM trusted boot hardware apply failed: " + " | ".join(details)


def _opentcsm_boot_failure_details(
    error: str,
    *,
    status: str | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = str(error or "")
    normalized_status = status or "failed"
    code = "TPCM_BOOT_HARDWARE_APPLY_FAILED"
    summary = "TPCM trusted boot hardware apply failed."
    if "precheck failed" in text or status == "precheck_failed":
        code = "TPCM_BOOT_PRECHECK_FAILED"
        summary = "TPCM trusted boot hardware apply precheck failed."
    elif "authorization material is not configured" in text:
        normalized_status = "authorization_missing"
        code = "TPCM_BOOT_AUTH_MISSING"
        summary = "TPCM boot authorization material is not configured."
    elif (
        "invalid OpenTCSM auth" in text
        or "must be a hex string" in text
        or "auth material" in text and "missing" in text
    ):
        normalized_status = "authorization_invalid"
        code = "TPCM_BOOT_AUTH_INVALID"
        summary = "TPCM boot authorization material is invalid."
    elif _looks_like_tpcm_auth_rejected(text):
        normalized_status = "authorization_rejected"
        code = "TPCM_BOOT_AUTH_REJECTED"
        summary = "TPCM rejected the boot policy authorization material."
    elif _looks_like_boot_reference_rejected(text):
        normalized_status = "reference_update_rejected"
        code = "TPCM_BOOT_REFERENCE_UPDATE_REJECTED"
        summary = (
            "TPCM rejected the boot reference update. Check UID, key material, "
            "current TPCM policy and reference operation."
        )
    elif _looks_like_opentcsm_boot_command_missing(text):
        normalized_status = "command_missing"
        code = "TPCM_BOOT_COMMAND_NOT_FOUND"
        summary = (
            "OpenTCSM trusted boot commands are not available on the node. "
            "Check update_bmeasure_references and set_measure_ctrl_switch."
        )
    elif "set_measure_ctrl_switch" in text:
        normalized_status = "control_failed"
        code = "TPCM_BOOT_CONTROL_SWITCH_FAILED"
        summary = "TPCM boot measurement control switch failed."
    elif status == "verify_failed" or "verify failed" in text:
        normalized_status = "verify_failed"
        code = "TPCM_BOOT_HARDWARE_VERIFY_FAILED"
        summary = "TPCM trusted boot hardware apply could not be verified."

    details: dict[str, Any] = {
        "tpcm_write_status": normalized_status,
        "tpcm_write_enabled": True,
        "tpcm_write_error_code": code,
        "tpcm_write_error_summary": summary,
        "tpcm_write_last_result": "failed",
        "tpcm_write_last_result_summary": summary,
        "tpcm_write_last_result_at": datetime.now(timezone.utc).isoformat(),
        "policy_last_result": "failed",
        "policy_last_result_summary": summary,
        "policy_last_result_at": datetime.now(timezone.utc).isoformat(),
        "policy_apply_error_code": code,
        "policy_apply_error_summary": summary,
    }
    if result:
        details["tpcm_hardware_apply_result"] = _summarize_apply_result(result)
        details["tpcm_hardware_apply_sha256"] = _content_hash(result)
    return details


def _looks_like_tpcm_auth_rejected(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\b(?:ret|rc)\s*[:=]\s*(?:0x0*98|152)\b", text, re.IGNORECASE):
        return True
    return "0x00000098" in lowered or "ret:0x98" in lowered or "ret: 0x98" in lowered


def _looks_like_boot_reference_rejected(text: str) -> bool:
    lowered = text.lower()
    if "update_bmeasure_references" not in lowered:
        return False
    if re.search(r"\b(?:ret|rc|error)\s*[:=]?\s*(?:0x0*88|136)\b", text, re.IGNORECASE):
        return True
    return "0x88" in lowered or "0x00000088" in lowered


def _looks_like_opentcsm_boot_command_missing(text: str) -> bool:
    lowered = text.lower()
    for command in ("update_bmeasure_references", "set_measure_ctrl_switch"):
        if (
            f"command not found: {command}" in lowered
            or f"no such file or directory: '{command}'" in lowered
            or f'no such file or directory: "{command}"' in lowered
            or f"{command} rc=127" in lowered
        ):
            return True
    return False


def _summarize_apply_result(result: dict[str, Any]) -> dict[str, Any]:
    commands = [
        {
            "name": item.get("name"),
            "command": item.get("command"),
            "rc": item.get("rc"),
        }
        for item in result.get("commands", [])
        if isinstance(item, dict)
    ]
    return {
        "hostname": result.get("hostname"),
        "applied_at": result.get("applied_at"),
        "auth_ref": result.get("auth_ref"),
        "auth_uid": result.get("auth_uid"),
        "operation": result.get("operation"),
        "enable_boot_control": result.get("enable_boot_control"),
        "ok": result.get("ok"),
        "error": result.get("error") or "",
        "commands": commands,
        "observed_before": result.get("observed_before", {}),
        "observed_after": result.get("observed_after", {}),
    }


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
