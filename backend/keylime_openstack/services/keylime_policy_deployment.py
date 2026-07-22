"""Keylime TPM and IMA policy deployment."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from keylime_openstack.config import Settings
from keylime_openstack.constants import POLICY_IMA_RUNTIME
from keylime_openstack.models import ComputeNode, TrustPolicy
from keylime_openstack.services.ansible import AnsibleExecutor
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.measured_boot_policies import (
    _event_log_fallback_mode,
    _fallback_pcrs,
    _measured_boot_pcrs,
    _parse_tpm2_pcrread_sha256,
    _tpm_policy_from_pcrs,
)
from keylime_openstack.services.policy_artifacts import _content_hash, _external_name
from keylime_openstack.services.policy_deployment_errors import AwaitingReboot

__all__ = ["KeylimePolicyDeployment", "KeylimePolicyDeploymentResult"]


@dataclass(frozen=True)
class KeylimePolicyDeploymentResult:
    external_name: str
    rendered_policy: dict[str, Any]
    deployment_details: dict[str, Any]
    response: dict[str, Any]


class KeylimePolicyDeployment:
    def __init__(
        self,
        settings: Settings,
        ansible: AnsibleExecutor,
        keylime: KeylimeClient,
        workspace_factory: Callable[[], AbstractContextManager[Path]],
        require_ansible_success: Callable[[int, str, str], None],
        require_keylime_success: Callable[[dict[str, Any]], None],
        active_external_policy_name: Callable[[int, str], str],
        active_boot_policy_adapter: Callable[[int], dict[str, Any]],
    ) -> None:
        self.settings = settings
        self.ansible = ansible
        self.keylime = keylime
        self.workspace_factory = workspace_factory
        self.require_ansible_success = require_ansible_success
        self.require_keylime_success = require_keylime_success
        self.active_external_policy_name = active_external_policy_name
        self.active_boot_policy_adapter = active_boot_policy_adapter

    def deploy_measured_boot(
        self,
        policy: TrustPolicy,
        node: ComputeNode,
    ) -> KeylimePolicyDeploymentResult:
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
        with self.workspace_factory() as workspace:
            event_path = workspace / "binary_bios_measurements"
            result = self.ansible.run(
                playbook="collect-measured-boot.yml",
                node=node,
                workspace=workspace,
                extra_vars={"evidence_output_path": str(event_path)},
            )
            self.require_ansible_success(result.rc, result.stdout, result.stderr)
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
                runtime_policy_name=self.active_external_policy_name(
                    node.id,
                    POLICY_IMA_RUNTIME,
                ),
                measured_boot_policy_name=external_name,
            )
            self.require_keylime_success(apply_result)
            deployment_details = {
                "keylime_artifact": "measured_boot_refstate",
                "evidence_type": "tpm_event_log",
                "evidence_sha256": event_log_sha256,
                "rendered_policy_sha256": _content_hash(reference_state),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "deployed_by": "keylime-tenant",
                "secure_boot_required": bool(
                    policy.content.get("secure_boot_required", True)
                ),
            }
            response = {
                "keylime_policy_name": external_name,
                "event_log_sha256": event_log_sha256,
                "policy_engine": policy.content.get("policy_engine"),
                "pcrs": policy.content.get("pcrs"),
            }
            return KeylimePolicyDeploymentResult(
                external_name=external_name,
                rendered_policy=reference_state,
                deployment_details=deployment_details,
                response=response,
            )

    def _deploy_measured_boot_pcr_quote(
        self,
        *,
        policy: TrustPolicy,
        node: ComputeNode,
        workspace: Path,
        event_log_sha256: str,
        measured_boot_error: str,
    ) -> KeylimePolicyDeploymentResult:
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
        self.require_ansible_success(result.rc, result.stdout, result.stderr)
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
            runtime_policy_name=self.active_external_policy_name(
                node.id,
                POLICY_IMA_RUNTIME,
            ),
        )
        self.require_keylime_success(apply_result)
        deployment_details = {
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
        }
        response = {
            "keylime_policy_name": "",
            "keylime_artifact": "tpm_pcr_quote_policy",
            "measured_boot_fallback": "pcr_quote",
            "pcrs": sorted(pcr_values),
            "measured_boot_pcrs": measured_boot_pcrs,
            "fallback_pcrs": selected_pcrs,
            "event_log_sha256": event_log_sha256,
            "pcr_policy_sha256": _content_hash(tpm_policy),
        }
        return KeylimePolicyDeploymentResult(
            external_name="",
            rendered_policy=tpm_policy,
            deployment_details=deployment_details,
            response=response,
        )

    def deploy_ima_runtime(
        self,
        policy: TrustPolicy,
        node: ComputeNode,
    ) -> KeylimePolicyDeploymentResult:
        with self.workspace_factory() as workspace:
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
            if result.rc != 0 and "requires a controlled reboot" in (
                result.stdout + result.stderr
            ):
                raise AwaitingReboot("IMA policy is installed and requires a controlled reboot")
            self.require_ansible_success(result.rc, result.stdout, result.stderr)
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
            boot_policy = self.active_boot_policy_adapter(node.id)
            apply_result = self.keylime.tenant_tool_apply_policy(
                agent_uuid=node.keylime_agent_uuid,
                agent_ip=node.keylime_agent_ip or node.management_ip,
                agent_port=node.keylime_agent_port,
                tpm_policy=boot_policy.get("tpm_policy"),
                runtime_policy_name=external_name,
                measured_boot_policy_name=boot_policy.get("measured_boot_policy_name", ""),
                disable_measured_boot=bool(boot_policy.get("disable_measured_boot")),
            )
            self.require_keylime_success(apply_result)
            deployment_details = {
                "keylime_artifact": "runtime_policy",
                "evidence_type": "ima_measurement_list",
                "evidence_sha256": hashlib.sha256(
                    measurements.encode("utf-8")
                ).hexdigest(),
                "rendered_policy_sha256": _content_hash(runtime_policy),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "deployed_by": "keylime-tenant",
                "measurement_count": len(measurements.splitlines()),
            }
            response = {
                "keylime_policy_name": external_name,
                "measurement_count": len(measurements.splitlines()),
                "runtime_policy_sha256": _content_hash(runtime_policy),
            }
            return KeylimePolicyDeploymentResult(
                external_name=external_name,
                rendered_policy=runtime_policy,
                deployment_details=deployment_details,
                response=response,
            )
