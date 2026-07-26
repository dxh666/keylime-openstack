"""OpenTCSM/TPCM dynamic measurement policy deployment."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import TRUST_AGENT_OPENTCSM_TPCM
from keylime_openstack.models import ComputeNode, TrustPolicy
from keylime_openstack.services.ansible import AnsibleExecutor
from keylime_openstack.services.opentcsm_collect import OpenTcsmCollector
from keylime_openstack.services.opentcsm_policy import (
    load_opentcsm_auth_material,
    opentcsm_auth_ref_for_profile,
)
from keylime_openstack.services.policy_artifacts import _content_hash, _external_name
from keylime_openstack.services.tpcm_dynamic_policies import (
    _dynamic_object_configs,
    _opentcsm_dynamic_apply_error,
    _safe_int,
)
from keylime_openstack.services.trust_agents import node_trusted_root
from keylime_openstack.services.trust_registration import ensure_trusted_node_profile

__all__ = ["TpcmDynamicDeployment", "TpcmDynamicDeploymentResult"]


@dataclass(frozen=True)
class TpcmDynamicDeploymentResult:
    external_name: str
    rendered_policy: dict[str, Any]
    deployment_details: dict[str, Any]
    response: dict[str, Any]


class TpcmDynamicDeployment:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        ansible: AnsibleExecutor,
        workspace_factory: Callable[[], AbstractContextManager[Path]],
        require_ansible_success: Callable[[int, str, str], None],
    ):
        self.session = session
        self.settings = settings
        self.ansible = ansible
        self.workspace_factory = workspace_factory
        self.require_ansible_success = require_ansible_success

    def deploy(self, policy: TrustPolicy, node: ComputeNode) -> TpcmDynamicDeploymentResult:
        profile = ensure_trusted_node_profile(self.session, node, self.settings)
        auth_ref = opentcsm_auth_ref_for_profile(
            profile,
            "dynamic",
            self.settings,
            policy.content,
        )
        auth = load_opentcsm_auth_material(
            self.settings,
            auth_ref,
        )
        with self.workspace_factory() as workspace:
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
                self.require_ansible_success(apply_result.rc, apply_result.stdout, apply_result.stderr)
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
        deployment_details = {
            "keylime_artifact": "opentcsm_dynamic_measurement_policy",
            "evidence_type": "tpcm_dynamic_measurement",
            "evidence_sha256": raw.get("trust_report_sha256", ""),
            "rendered_policy_sha256": _content_hash(rendered_policy),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "deployed_by": "opentcsm-policy-executor",
            "trust_agent_type": TRUST_AGENT_OPENTCSM_TPCM,
            "trusted_root": node_trusted_root(node, self.settings),
            "node_dynamic_measure_enabled": node_dynamic_measure_enabled,
            "auth_ref": auth.ref,
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
        }
        response = {
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
            "auth_ref": auth.ref,
            "auth_uid": auth.uid,
            "auth_public_key_sha256": auth.public_fingerprint,
            "policy_last_result": "success",
            "policy_last_result_summary": "TPCM 动态度量策略已生效。",
        }
        return TpcmDynamicDeploymentResult(
            external_name=external_name,
            rendered_policy=rendered_policy,
            deployment_details=deployment_details,
            response=response,
        )
