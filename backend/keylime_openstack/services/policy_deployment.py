"""Deploy managed policies through Ansible and bind them in Keylime."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    POLICY_DEPLOY_APPLIED,
    POLICY_DEPLOY_APPLYING,
    POLICY_DEPLOY_AWAITING_REBOOT,
    POLICY_DEPLOY_EXTERNAL_PENDING,
    POLICY_DEPLOY_FAILED,
    POLICY_DEPLOY_SUPERSEDED,
    POLICY_IMA_RUNTIME,
    POLICY_MEASURED_BOOT,
    POLICY_TPCM_DYNAMIC_MEASUREMENT,
    TRUST_AGENT_KEYLIME,
    TRUST_AGENT_OPENTCSM_TPCM,
)
from keylime_openstack.models import AuditEvent, ComputeNode, PolicyBinding, TrustPolicy
from keylime_openstack.services.ansible import AnsibleExecutor
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.opentcsm_collect import OpenTcsmCollector
from keylime_openstack.services.opentcsm_policy import (
    OPEN_TCSM_DMEASURE_OBJECTS,
    load_opentcsm_auth_material,
)
from keylime_openstack.services.policy import canonical_policy_type, load_policy
from keylime_openstack.services.trust_agents import (
    node_trust_agent_name,
    node_trust_agent_type,
    node_trusted_root,
)


class PolicyDeploymentService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.ansible = AnsibleExecutor(settings)
        self.keylime = KeylimeClient(settings)

    def deploy(self, policy_id: int, binding_id: int | None = None) -> dict[str, Any]:
        policy = load_policy(self.session, policy_id)
        if not policy:
            raise RuntimeError(f"unknown policy {policy_id}")

        results: list[dict[str, Any]] = []
        for binding in policy.bindings:
            if binding_id is not None and binding.id != binding_id:
                continue
            if not binding.active:
                continue
            node = self.session.get(ComputeNode, binding.target_id)
            if not node:
                self._failed(binding, f"node {binding.target_id} no longer exists")
                results.append(self._binding_result(binding, "failed"))
                continue
            binding.application_status = POLICY_DEPLOY_APPLYING
            binding.last_error = ""
            self.session.flush()
            policy_type = canonical_policy_type(policy.policy_type)
            agent_type = node_trust_agent_type(node, self.settings)
            try:
                if agent_type == TRUST_AGENT_OPENTCSM_TPCM and policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT:
                    details = self._deploy_opentcsm_dynamic_measurement(policy, binding, node)
                elif agent_type != TRUST_AGENT_KEYLIME:
                    details = self._defer_external_trust_agent_policy(policy, binding, node)
                elif policy_type == POLICY_MEASURED_BOOT:
                    details = self._deploy_measured_boot(policy, binding, node)
                elif policy_type == POLICY_IMA_RUNTIME:
                    details = self._deploy_ima_runtime(policy, binding, node)
                elif policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT:
                    raise RuntimeError("TPCM 动态度量策略仅支持 OpenTCSM/TPCM 节点")
                else:
                    raise RuntimeError(f"deployment is not implemented for {policy.policy_type}")
            except AwaitingReboot as exc:
                binding.application_status = POLICY_DEPLOY_AWAITING_REBOOT
                binding.last_error = str(exc)
                details = {"reason": str(exc)}
            except Exception as exc:  # pragma: no cover - remote execution boundary
                error = str(exc)
                failure_details = (
                    _opentcsm_dynamic_failure_details(error)
                    if agent_type == TRUST_AGENT_OPENTCSM_TPCM
                    and policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT
                    else {}
                )
                if failure_details:
                    failure_details.update(_dynamic_policy_context(policy.content))
                self._failed(binding, error, failure_details)
                details = {"error": error, **failure_details}
            self._audit(policy.name, policy_type, node.hostname, binding, details)
            results.append(self._binding_result(binding, binding.application_status))
            self.session.flush()
        if binding_id is not None and not results:
            raise RuntimeError(f"policy binding {binding_id} is not active or does not exist")
        return {
            "policy_id": policy.id,
            "policy_name": policy.name,
            "policy_type": policy.policy_type,
            "binding_id": binding_id,
            "bindings": results,
            "failed": sum(item["status"] == POLICY_DEPLOY_FAILED for item in results),
            "external_pending": sum(
                item["status"] == POLICY_DEPLOY_EXTERNAL_PENDING for item in results
            ),
            "awaiting_reboot": sum(
                item["status"] == POLICY_DEPLOY_AWAITING_REBOOT for item in results
            ),
        }

    def _defer_external_trust_agent_policy(
        self,
        policy: TrustPolicy,
        binding: PolicyBinding,
        node: ComputeNode,
    ) -> dict[str, Any]:
        agent_type = node_trust_agent_type(node, self.settings)
        if agent_type != TRUST_AGENT_OPENTCSM_TPCM:
            raise RuntimeError(f"unsupported trust agent type {agent_type!r}")
        binding.application_status = POLICY_DEPLOY_EXTERNAL_PENDING
        binding.last_error = ""
        binding.binding_details = {
            **dict(binding.binding_details or {}),
            "policy_type": canonical_policy_type(policy.policy_type),
            "trust_agent_type": agent_type,
            "trust_agent_name": node_trust_agent_name(node, self.settings),
            "trusted_root": node_trusted_root(node, self.settings),
            "deployed_by": "external-trust-agent",
            "reason": "OpenTCSM/Hygon TPCM policy deployment is not implemented yet.",
        }
        return {
            "trust_agent_type": agent_type,
            "trust_agent_name": node_trust_agent_name(node, self.settings),
            "trusted_root": node_trusted_root(node, self.settings),
            "policy_deployment": "external_pending",
            "message": "OpenTCSM/Hygon TPCM policy deployment is waiting for adapter implementation.",
        }

    def _deploy_measured_boot(
        self,
        policy: TrustPolicy,
        binding: PolicyBinding,
        node: ComputeNode,
    ) -> dict[str, Any]:
        configured_engine = self.settings.keylime_measured_boot_policy_engine.strip()
        requested_engine = str(policy.content.get("policy_engine") or "").strip()
        if not requested_engine or requested_engine == "configured":
            requested_engine = configured_engine
        if configured_engine != requested_engine:
            raise RuntimeError(
                "Measured Boot policy engine mismatch: "
                f"control plane expects {requested_engine!r}, verifier is configured as "
                f"{configured_engine!r}"
            )
        if configured_engine == "accept-all":
            raise RuntimeError("accept-all cannot be used for managed Measured Boot policies")
        with self._workspace() as workspace:
            event_path = workspace / "binary_bios_measurements"
            result = self.ansible.run(
                playbook="collect-measured-boot.yml",
                node=node,
                workspace=workspace,
                extra_vars={"evidence_output_path": str(event_path)},
            )
            self._require_ansible_success(result.rc, result.stdout, result.stderr)
            event_log = event_path.read_bytes()
            if not event_log:
                raise RuntimeError("collected TPM measured boot event log is empty")
            event_log_sha256 = hashlib.sha256(event_log).hexdigest()

            reference_state = policy.content.get("reference_state")
            if not reference_state:
                try:
                    reference_state = self.keylime.tenant_tool_create_measured_boot_refstate(
                        event_log,
                        secure_boot_required=bool(policy.content.get("secure_boot_required", True)),
                    )
                except RuntimeError as exc:
                    if _event_log_fallback_mode(policy) != "pcr_quote":
                        raise
                    return self._deploy_measured_boot_pcr_quote(
                        policy=policy,
                        binding=binding,
                        node=node,
                        workspace=workspace,
                        event_log_sha256=event_log_sha256,
                        measured_boot_error=str(exc),
                    )
            external_name = _external_name("mb", policy.name, node.hostname, reference_state)
            self.keylime.tenant_tool_store_measured_boot_policy(
                name=external_name,
                reference_state=reference_state,
            )
            apply_result = self.keylime.tenant_tool_apply_policy(
                agent_uuid=node.keylime_agent_uuid,
                agent_ip=node.keylime_agent_ip or node.management_ip,
                agent_port=node.keylime_agent_port,
                runtime_policy_name=self._active_external_policy_name(
                    node.id,
                    POLICY_IMA_RUNTIME,
                ),
                measured_boot_policy_name=external_name,
            )
            self._require_keylime_success(apply_result)
            self._applied(
                binding,
                policy,
                external_name,
                reference_state,
                deployment_details={
                    "keylime_artifact": "measured_boot_refstate",
                    "evidence_type": "tpm_event_log",
                    "evidence_sha256": event_log_sha256,
                    "rendered_policy_sha256": _content_hash(reference_state),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "deployed_by": "keylime-tenant",
                    "secure_boot_required": bool(
                        policy.content.get("secure_boot_required", True)
                    ),
                },
            )
            return {
                "keylime_policy_name": external_name,
                "event_log_sha256": event_log_sha256,
                "policy_engine": policy.content.get("policy_engine"),
                "pcrs": policy.content.get("pcrs"),
            }

    def _deploy_measured_boot_pcr_quote(
        self,
        *,
        policy: TrustPolicy,
        binding: PolicyBinding,
        node: ComputeNode,
        workspace: Path,
        event_log_sha256: str,
        measured_boot_error: str,
    ) -> dict[str, Any]:
        pcr_output_path = workspace / "tpm-pcr-sha256.txt"
        measured_boot_pcrs = _measured_boot_pcrs(policy)
        selected_pcrs = _fallback_pcrs(policy)
        result = self.ansible.run(
            playbook="collect-tpm-pcrs.yml",
            node=node,
            workspace=workspace,
            extra_vars={
                "evidence_output_path": str(pcr_output_path),
                "pcr_bank": "sha256",
                "pcr_selection": ",".join(str(item) for item in selected_pcrs),
            },
        )
        self._require_ansible_success(result.rc, result.stdout, result.stderr)
        pcr_output = pcr_output_path.read_text(encoding="utf-8")
        pcr_values = _parse_tpm2_pcrread_sha256(pcr_output, selected_pcrs)
        tpm_policy = _tpm_policy_from_pcrs(pcr_values)
        apply_result = self.keylime.tenant_tool_apply_policy(
            agent_uuid=node.keylime_agent_uuid,
            agent_ip=node.keylime_agent_ip or node.management_ip,
            agent_port=node.keylime_agent_port,
            tpm_policy=tpm_policy,
            disable_measured_boot=True,
            replace_existing=True,
            runtime_policy_name=self._active_external_policy_name(
                node.id,
                POLICY_IMA_RUNTIME,
            ),
        )
        self._require_keylime_success(apply_result)
        self._applied(
            binding,
            policy,
            "",
            tpm_policy,
            deployment_details={
                "keylime_artifact": "tpm_pcr_quote_policy",
                "evidence_type": "tpm_pcr_quote",
                "evidence_sha256": hashlib.sha256(pcr_output.encode("utf-8")).hexdigest(),
                "event_log_sha256": event_log_sha256,
                "rendered_policy_sha256": _content_hash(tpm_policy),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "deployed_by": "keylime-tenant",
                "measured_boot_fallback": "pcr_quote",
                "measured_boot_refstate_error": measured_boot_error[:2000],
                "pcr_bank": "sha256",
                "pcrs": sorted(pcr_values),
                "measured_boot_pcrs": measured_boot_pcrs,
                "fallback_pcrs": selected_pcrs,
            },
        )
        return {
            "keylime_policy_name": "",
            "keylime_artifact": "tpm_pcr_quote_policy",
            "measured_boot_fallback": "pcr_quote",
            "pcrs": sorted(pcr_values),
            "measured_boot_pcrs": measured_boot_pcrs,
            "fallback_pcrs": selected_pcrs,
            "event_log_sha256": event_log_sha256,
            "pcr_policy_sha256": _content_hash(tpm_policy),
        }

    def _deploy_ima_runtime(
        self,
        policy: TrustPolicy,
        binding: PolicyBinding,
        node: ComputeNode,
    ) -> dict[str, Any]:
        with self._workspace() as workspace:
            measurement_path = workspace / "ascii_runtime_measurements"
            result = self.ansible.run(
                playbook="apply-ima-policy.yml",
                node=node,
                workspace=workspace,
                extra_vars={
                    "evidence_output_path": str(measurement_path),
                    "node_ima_policy": policy.content["node_ima_policy"],
                    "reboot_after_apply": policy.content.get("reboot_after_apply", False),
                },
            )
            if result.rc != 0 and "requires a controlled reboot" in (result.stdout + result.stderr):
                raise AwaitingReboot("IMA policy is installed and requires a controlled reboot")
            self._require_ansible_success(result.rc, result.stdout, result.stderr)
            measurements = measurement_path.read_text(encoding="utf-8")
            if not measurements.strip():
                raise RuntimeError("collected IMA runtime measurement list is empty")

            runtime_policy = self.keylime.tenant_tool_create_runtime_policy(
                measurements,
                policy.excludes,
            )
            external_name = _external_name("ima", policy.name, node.hostname, runtime_policy)
            self.keylime.tenant_tool_store_runtime_policy(
                name=external_name,
                runtime_policy=runtime_policy,
            )
            boot_policy = self._active_boot_policy_adapter(node.id)
            apply_result = self.keylime.tenant_tool_apply_policy(
                agent_uuid=node.keylime_agent_uuid,
                agent_ip=node.keylime_agent_ip or node.management_ip,
                agent_port=node.keylime_agent_port,
                tpm_policy=boot_policy.get("tpm_policy"),
                runtime_policy_name=external_name,
                measured_boot_policy_name=boot_policy.get("measured_boot_policy_name", ""),
                disable_measured_boot=bool(boot_policy.get("disable_measured_boot")),
            )
            self._require_keylime_success(apply_result)
            self._applied(
                binding,
                policy,
                external_name,
                runtime_policy,
                deployment_details={
                    "keylime_artifact": "runtime_policy",
                    "evidence_type": "ima_measurement_list",
                    "evidence_sha256": hashlib.sha256(measurements.encode("utf-8")).hexdigest(),
                    "rendered_policy_sha256": _content_hash(runtime_policy),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "deployed_by": "keylime-tenant",
                    "measurement_count": len(measurements.splitlines()),
                },
            )
            return {
                "keylime_policy_name": external_name,
                "measurement_count": len(measurements.splitlines()),
                "runtime_policy_sha256": _content_hash(runtime_policy),
            }

    def _deploy_opentcsm_dynamic_measurement(
        self,
        policy: TrustPolicy,
        binding: PolicyBinding,
        node: ComputeNode,
    ) -> dict[str, Any]:
        auth = load_opentcsm_auth_material(
            self.settings,
            str(policy.content.get("auth_material_ref") or ""),
        )
        with self._workspace() as workspace:
            apply_result_path = workspace / "opentcsm-dynamic-apply.json"
            object_configs = _dynamic_object_configs(policy.content)
            node_dynamic_measure_enabled = bool(
                policy.content.get(
                    "node_dynamic_measure_enabled",
                    policy.content.get("dynamic_measure_required", True),
                )
            )
            environment_objects = [
                name
                for name, config in object_configs.items()
                if node_dynamic_measure_enabled and config["enabled"]
            ]
            close_disabled_fixed_objects = True
            dynamic_policy = {
                "node_dynamic_measure_enabled": node_dynamic_measure_enabled,
                "environment_object_configs": object_configs,
                "environment_objects": environment_objects,
                "environment_interval_milli": int(
                    policy.content.get("environment_interval_milli") or 60000
                ),
                "delete_unmanaged_objects": close_disabled_fixed_objects,
            }
            apply_result = self.ansible.run(
                playbook="apply-opentcsm-dynamic-policy.yml",
                node=node,
                workspace=workspace,
                extra_vars={
                    "result_output_path": str(apply_result_path),
                    "opentcsm_dynamic_policy": dynamic_policy,
                    "opentcsm_dynamic_auth": auth.playbook_vars(),
                },
            )
            opentcsm_apply: dict[str, Any] | None = None
            if apply_result_path.is_file():
                try:
                    opentcsm_apply = json.loads(apply_result_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    if apply_result.rc == 0:
                        raise RuntimeError(
                            "OpenTCSM dynamic policy apply result is not valid JSON"
                        ) from exc
            if apply_result.rc != 0:
                if opentcsm_apply:
                    raise RuntimeError(_opentcsm_dynamic_apply_error(opentcsm_apply))
                self._require_ansible_success(apply_result.rc, apply_result.stdout, apply_result.stderr)
            if not apply_result_path.is_file():
                raise RuntimeError("OpenTCSM dynamic policy apply result was not produced")
            if opentcsm_apply is None:
                try:
                    opentcsm_apply = json.loads(apply_result_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "OpenTCSM dynamic policy apply result is not valid JSON"
                    ) from exc

        result = OpenTcsmCollector(self.session, self.settings).collect(node)
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        failures = dict(raw.get("trust_report_failures") or {})
        dynamic_baselines = _safe_int(raw.get("dynamic_measure_ref_number"))
        require_clean_report = bool(policy.content.get("require_clean_trust_report", False))
        desired_objects = [str(item) for item in environment_objects]
        desired_intervals = {
            name: int(config["interval_milli"])
            for name, config in object_configs.items()
            if config["enabled"]
        }
        disabled_objects = {
            name
            for name, config in object_configs.items()
            if not node_dynamic_measure_enabled or not config["enabled"]
        }
        default_interval = int(policy.content.get("environment_interval_milli") or 60000)
        observed_dmeasure_policy = [
            item
            for item in (raw.get("dmeasure_policy") or [])
            if isinstance(item, dict)
        ]
        observed_by_object = {
            str(item.get("object")): item
            for item in observed_dmeasure_policy
            if item.get("object")
        }
        dynamic_on = raw.get("dynamic_measure_on") is True
        dynamic_status = result.get("dynamic_measurement_status")
        violations: list[str] = []
        if require_clean_report and failures:
            violations.append("TPCM 可信报告存在失败计数")
        for object_name in desired_objects:
            observed = observed_by_object.get(object_name)
            if not observed:
                violations.append(f"TPCM 动态度量对象未生效：{object_name}")
                continue
            desired_interval = desired_intervals[object_name]
            if _safe_int(observed.get("interval_milli")) != desired_interval:
                violations.append(
                    f"TPCM 动态度量周期不一致：{object_name} 当前 "
                    f"{observed.get('interval_milli')}ms，要求 {desired_interval}ms"
                )
        if close_disabled_fixed_objects:
            for object_name in sorted(disabled_objects):
                if object_name in observed_by_object:
                    violations.append(f"TPCM 动态度量对象未关闭：{object_name}")
        if violations:
            raise RuntimeError("OpenTCSM 动态度量策略校验未通过：" + "；".join(violations))

        rendered_policy = {
            "policy": {
                "require_clean_trust_report": require_clean_report,
                "environment_object_configs": object_configs,
                "environment_objects": desired_objects,
                "environment_interval_milli": default_interval,
                "delete_unmanaged_objects": close_disabled_fixed_objects,
            },
            "observed": {
                "trust_root": result.get("trust_root"),
                "agent_name": result.get("agent_name"),
                "trusted": result.get("trusted"),
                "dynamic_measure_on": dynamic_on,
                "dynamic_measurement_status": dynamic_status,
                "dmeasure_policy": observed_dmeasure_policy,
                "dynamic_measure_ref_number": dynamic_baselines,
                "dmeasure_times": raw.get("dmeasure_times"),
                "trust_report_sha256": raw.get("trust_report_sha256", ""),
                "policy_report_sha256": raw.get("policy_report_sha256", ""),
                "global_control_policy_sha256": raw.get("global_control_policy_sha256", ""),
                "dmeasure_policy_sha256": raw.get("dmeasure_policy_sha256", ""),
                "trust_report_failures": failures,
            },
            "apply_result": {
                "applied_at": opentcsm_apply.get("applied_at"),
                "observed_before": opentcsm_apply.get("observed_before", []),
                "observed_after": opentcsm_apply.get("observed_after", []),
                "ok": opentcsm_apply.get("ok"),
            },
            "auth": auth.metadata(),
        }
        external_name = _external_name("tpcm-dyn", policy.name, node.hostname, rendered_policy)
        self._applied(
            binding,
            policy,
            external_name,
            rendered_policy,
            deployment_details={
                "keylime_artifact": "opentcsm_dynamic_measurement_policy",
                "evidence_type": "tpcm_dynamic_measurement",
                "evidence_sha256": raw.get("trust_report_sha256", ""),
                "rendered_policy_sha256": _content_hash(rendered_policy),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "deployed_by": "opentcsm-policy-executor",
                "trust_agent_type": TRUST_AGENT_OPENTCSM_TPCM,
                "trusted_root": node_trusted_root(node, self.settings),
                "node_dynamic_measure_enabled": node_dynamic_measure_enabled,
                "auth_material_ref": auth.ref,
                "auth_uid": auth.uid,
                "auth_public_key_sha256": auth.public_fingerprint,
                "policy_apply_status": "consistent",
                "policy_apply_authorization_status": "normal",
                "policy_apply_error_code": "",
                "policy_apply_error_summary": "",
                "policy_last_result": "success",
                "policy_last_result_summary": "TPCM 动态度量策略已生效。",
                "policy_last_result_at": datetime.now(timezone.utc).isoformat(),
                "dynamic_measure_on": dynamic_on,
                "dmeasure_policy_sha256": raw.get("dmeasure_policy_sha256", ""),
                "dynamic_measure_ref_number": dynamic_baselines,
                "trust_report_clean": not failures,
            },
        )
        return {
            "external_policy_name": external_name,
            "keylime_artifact": "opentcsm_dynamic_measurement_policy",
            "node_dynamic_measure_enabled": node_dynamic_measure_enabled,
            "dynamic_measure_on": dynamic_on,
            "dynamic_measurement_status": dynamic_status,
            "environment_objects": desired_objects,
            "environment_object_configs": object_configs,
            "environment_interval_milli": default_interval,
            "dynamic_measure_ref_number": dynamic_baselines,
            "trust_report_sha256": raw.get("trust_report_sha256", ""),
            "dmeasure_policy_sha256": raw.get("dmeasure_policy_sha256", ""),
            "policy_sha256": _content_hash(rendered_policy),
            "policy_apply_status": "consistent",
            "policy_apply_authorization_status": "normal",
            "policy_last_result": "success",
            "policy_last_result_summary": "TPCM 动态度量策略已生效。",
        }

    def _applied(
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

    def _active_external_policy_name(self, node_id: int, policy_type: str) -> str:
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

    def _active_boot_policy_adapter(self, node_id: int) -> dict[str, Any]:
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
    def _failed(
        binding: PolicyBinding,
        error: str,
        extra_details: dict[str, Any] | None = None,
    ) -> None:
        binding.application_status = POLICY_DEPLOY_FAILED
        binding.last_error = error[:4000]
        details = {
            **dict(binding.binding_details or {}),
            "policy_apply_status": POLICY_DEPLOY_FAILED,
            "policy_apply_error_summary": error[:500],
            "policy_last_result": "failed",
            "policy_last_result_summary": error[:500],
            "policy_last_result_at": datetime.now(timezone.utc).isoformat(),
        }
        if extra_details:
            details.update(extra_details)
        binding.binding_details = details

    def _audit(
        self,
        policy_name: str,
        policy_type: str,
        hostname: str,
        binding: PolicyBinding,
        details: dict[str, Any],
    ) -> None:
        is_dynamic = policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT
        event_details = {
            "binding_id": binding.id,
            "executor": binding.executor,
            **details,
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
        self.session.add(
            AuditEvent(
                event_type="tpcm_dynamic_policy_apply" if is_dynamic else "policy_deploy",
                target=f"{policy_name}:{hostname}",
                severity=(
                    "info"
                    if binding.application_status == POLICY_DEPLOY_APPLIED
                    else "warning"
                ),
                message=(
                    details.get("policy_apply_error_summary")
                    if is_dynamic and binding.application_status != POLICY_DEPLOY_APPLIED
                    else f"policy deployment {binding.application_status}"
                ),
                event_details=event_details,
            )
        )

    @staticmethod
    def _binding_result(binding: PolicyBinding, status: str) -> dict[str, Any]:
        return {
            "binding_id": binding.id,
            "target_id": binding.target_id,
            "status": status,
            "external_policy_name": binding.external_policy_name,
            "error": binding.last_error,
        }

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
        raise RuntimeError(detail)

    @staticmethod
    def _require_keylime_success(result: dict[str, Any]) -> None:
        if result.get("rc") == 0:
            return
        command = result.get("command")
        command_text = " ".join(str(item) for item in command) if isinstance(command, list) else ""
        detail = str(result.get("stderr") or result.get("stdout") or "Keylime failed")
        parts = [
            f"Keylime tenant command failed rc={result.get('rc')}",
            f"command={command_text}" if command_text else "",
            f"detail={detail}",
        ]
        raise RuntimeError("; ".join(part for part in parts if part)[-4000:])

    @contextmanager
    def _workspace(self):
        self.settings.temp_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            yield Path(tmp)


class AwaitingReboot(RuntimeError):
    pass


def _content_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _external_name(prefix: str, policy_name: str, hostname: str, value: dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{policy_name}-{hostname}".lower()).strip("-")
    return f"klos-{prefix}-{slug[:180]}-{_content_hash(value)[:12]}"


def _event_log_fallback_mode(policy: TrustPolicy) -> str:
    value = str(policy.content.get("event_log_fallback") or "pcr_quote")
    normalized = value.strip().lower().replace("-", "_")
    return "pcr_quote" if normalized in {"pcr_quote", "tpm_pcr", "tpm_pcr_quote"} else "none"


def _opentcsm_dynamic_apply_error(result: dict[str, Any]) -> str:
    failed = [
        item
        for item in result.get("commands", [])
        if isinstance(item, dict) and int(item.get("rc") or 0) != 0
    ]
    if not failed:
        return str(result.get("error") or "OpenTCSM 动态度量策略生效失败")
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
    return "OpenTCSM 动态度量策略生效失败：" + " | ".join(details)


def _opentcsm_dynamic_failure_details(error: str) -> dict[str, Any]:
    text = str(error or "")
    status = "unknown"
    code = "TPCM_DYNAMIC_APPLY_FAILED"
    summary = "TPCM 动态度量策略生效失败。"

    if "authorization material is not configured" in text:
        status = "missing"
        code = "TPCM_AUTH_MISSING"
        summary = "TPCM 策略生效授权材料未配置。"
    elif (
        "invalid OpenTCSM auth" in text
        or ("auth material" in text and "missing" in text)
        or "must be a hex string" in text
    ):
        status = "invalid"
        code = "TPCM_AUTH_INVALID"
        summary = "TPCM 策略生效授权材料格式无效。"
    elif _looks_like_tpcm_auth_rejected(text):
        status = "rejected"
        code = "TPCM_AUTH_REJECTED"
        summary = "TPCM 拒绝了策略生效授权，请检查 UID 和授权证书/密钥是否已在节点注册。"
    elif "校验未通过" in text or "validation" in text.lower():
        status = "normal"
        code = "TPCM_DYNAMIC_POLICY_NOT_CONSISTENT"
        summary = "TPCM 动态度量策略已执行，但节点当前状态与目标配置不一致。"
    elif "Ansible returned" in text or "OpenTCSM dynamic policy apply result" in text:
        status = "unknown"
        code = "TPCM_COMMAND_FAILED"
        summary = "OpenTCSM 策略生效命令执行异常。"

    return {
        "policy_apply_status": POLICY_DEPLOY_FAILED,
        "policy_apply_authorization_status": status,
        "policy_apply_error_code": code,
        "policy_apply_error_summary": summary,
    }


def _looks_like_tpcm_auth_rejected(text: str) -> bool:
    if re.search(r"\b(?:ret|rc)\s*[:=]\s*(?:0x0*98|152)\b", text, re.IGNORECASE):
        return True
    if "0x00000098" in text or "ret:0x98" in text or "ret: 0x98" in text:
        return True
    return False


def _dynamic_audit_object_name(details: dict[str, Any]) -> str:
    if details.get("node_dynamic_measure_enabled") is False:
        return "全部动态度量对象"
    configs = details.get("environment_object_configs")
    if isinstance(configs, dict) and configs:
        enabled = [
            name
            for name, config in configs.items()
            if isinstance(config, dict) and config.get("enabled") is True
        ]
        return ",".join(enabled) if enabled else "none"
    objects = details.get("environment_objects")
    if isinstance(objects, list) and objects:
        return ",".join(str(item) for item in objects)
    return "环境动态度量策略"


def _dynamic_policy_context(content: dict[str, Any]) -> dict[str, Any]:
    object_configs = _dynamic_object_configs(content)
    node_dynamic_measure_enabled = bool(
        content.get(
            "node_dynamic_measure_enabled",
            content.get("dynamic_measure_required", True),
        )
    )
    environment_objects = [
        name
        for name, config in object_configs.items()
        if node_dynamic_measure_enabled and config["enabled"]
    ]
    return {
        "node_dynamic_measure_enabled": node_dynamic_measure_enabled,
        "environment_object_configs": object_configs,
        "environment_objects": environment_objects,
        "environment_interval_milli": int(content.get("environment_interval_milli") or 60000),
        "keylime_artifact": "opentcsm_dynamic_measurement_policy",
    }


def _dynamic_object_configs(content: dict[str, Any]) -> dict[str, dict[str, int | bool]]:
    default_interval = int(content.get("environment_interval_milli") or 60000)
    raw_configs = content.get("environment_object_configs")
    if isinstance(raw_configs, dict) and raw_configs:
        return {
            name: {
                "enabled": bool((raw_configs.get(name) or {}).get("enabled", False)),
                "interval_milli": int(
                    (raw_configs.get(name) or {}).get("interval_milli") or default_interval
                ),
            }
            for name in OPEN_TCSM_DMEASURE_OBJECTS
        }
    enabled_objects = set(content.get("environment_objects") or OPEN_TCSM_DMEASURE_OBJECTS)
    return {
        name: {
            "enabled": name in enabled_objects,
            "interval_milli": default_interval,
        }
        for name in OPEN_TCSM_DMEASURE_OBJECTS
    }


def _measured_boot_pcrs(policy: TrustPolicy) -> list[int]:
    raw = policy.content.get("pcrs") or list(range(8))
    try:
        selected = sorted({int(item) for item in raw})
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid measured boot PCR selection") from exc
    if not selected or selected[0] < 0 or selected[-1] > 23:
        raise RuntimeError("measured boot PCR selection must be between 0 and 23")
    return selected


def _fallback_pcrs(policy: TrustPolicy) -> list[int]:
    raw = policy.content.get("fallback_pcrs") or [7]
    try:
        selected = sorted({int(item) for item in raw})
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid fallback PCR selection for trusted boot policy") from exc
    if not selected or selected[0] < 0 or selected[-1] > 23:
        raise RuntimeError("fallback PCR selection must be between 0 and 23")
    return selected


def _parse_tpm2_pcrread_sha256(output: str, selected_pcrs: list[int]) -> dict[int, str]:
    values: dict[int, str] = {}
    for line in output.splitlines():
        match = re.match(r"\s*([0-9]+)\s*:\s*0x([0-9A-Fa-f]{64})\s*$", line)
        if match:
            values[int(match.group(1))] = match.group(2).lower()
    missing = [item for item in selected_pcrs if item not in values]
    if missing:
        raise RuntimeError(f"tpm2_pcrread output is missing sha256 PCR values: {missing}")
    return {item: values[item] for item in selected_pcrs}


def _tpm_policy_from_pcrs(pcr_values: dict[int, str]) -> dict[str, Any]:
    mask = 0
    policy: dict[str, Any] = {}
    for pcr, digest in sorted(pcr_values.items()):
        mask |= 1 << pcr
        policy[str(pcr)] = [digest.lower()]
    policy["mask"] = hex(mask)
    return policy


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
