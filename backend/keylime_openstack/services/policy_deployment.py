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
    POLICY_DEPLOY_FAILED,
    POLICY_DEPLOY_SUPERSEDED,
    POLICY_IMA_RUNTIME,
    POLICY_MEASURED_BOOT,
)
from keylime_openstack.models import AuditEvent, ComputeNode, PolicyBinding, TrustPolicy
from keylime_openstack.services.ansible import AnsibleExecutor
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.policy import canonical_policy_type, load_policy


class PolicyDeploymentService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.ansible = AnsibleExecutor(settings)
        self.keylime = KeylimeClient(settings)

    def deploy(self, policy_id: int) -> dict[str, Any]:
        policy = load_policy(self.session, policy_id)
        if not policy:
            raise RuntimeError(f"unknown policy {policy_id}")

        results: list[dict[str, Any]] = []
        for binding in policy.bindings:
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
            try:
                policy_type = canonical_policy_type(policy.policy_type)
                if policy_type == POLICY_MEASURED_BOOT:
                    details = self._deploy_measured_boot(policy, binding, node)
                elif policy_type == POLICY_IMA_RUNTIME:
                    details = self._deploy_ima_runtime(policy, binding, node)
                else:
                    raise RuntimeError(f"deployment is not implemented for {policy.policy_type}")
            except AwaitingReboot as exc:
                binding.application_status = POLICY_DEPLOY_AWAITING_REBOOT
                binding.last_error = str(exc)
                details = {"reason": str(exc)}
            except Exception as exc:  # pragma: no cover - remote execution boundary
                self._failed(binding, str(exc))
                details = {"error": str(exc)}
            self._audit(policy.name, node.hostname, binding, details)
            results.append(self._binding_result(binding, binding.application_status))
            self.session.flush()
        return {
            "policy_id": policy.id,
            "policy_name": policy.name,
            "policy_type": policy.policy_type,
            "bindings": results,
            "failed": sum(item["status"] == POLICY_DEPLOY_FAILED for item in results),
            "awaiting_reboot": sum(
                item["status"] == POLICY_DEPLOY_AWAITING_REBOOT for item in results
            ),
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

            reference_state = policy.content.get("reference_state")
            if not reference_state:
                reference_state = self.keylime.tenant_tool_create_measured_boot_refstate(event_log)
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
            self._applied(binding, policy, external_name, reference_state)
            return {
                "keylime_policy_name": external_name,
                "event_log_sha256": hashlib.sha256(event_log).hexdigest(),
                "policy_engine": policy.content.get("policy_engine"),
                "pcrs": policy.content.get("pcrs"),
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
            apply_result = self.keylime.tenant_tool_apply_policy(
                agent_uuid=node.keylime_agent_uuid,
                agent_ip=node.keylime_agent_ip or node.management_ip,
                agent_port=node.keylime_agent_port,
                runtime_policy_name=external_name,
                measured_boot_policy_name=self._active_external_policy_name(
                    node.id,
                    POLICY_MEASURED_BOOT,
                ),
            )
            self._require_keylime_success(apply_result)
            self._applied(binding, policy, external_name, runtime_policy)
            return {
                "keylime_policy_name": external_name,
                "measurement_count": len(measurements.splitlines()),
                "runtime_policy_sha256": _content_hash(runtime_policy),
            }

    def _applied(
        self,
        binding: PolicyBinding,
        policy: TrustPolicy,
        external_name: str,
        rendered_policy: dict[str, Any],
    ) -> None:
        binding.application_status = POLICY_DEPLOY_APPLIED
        binding.external_policy_name = external_name
        binding.rendered_policy = rendered_policy
        binding.applied_at = datetime.now(timezone.utc)
        binding.last_error = ""
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
        return binding.external_policy_name if binding else ""

    @staticmethod
    def _failed(binding: PolicyBinding, error: str) -> None:
        binding.application_status = POLICY_DEPLOY_FAILED
        binding.last_error = error[:4000]

    def _audit(
        self,
        policy_name: str,
        hostname: str,
        binding: PolicyBinding,
        details: dict[str, Any],
    ) -> None:
        self.session.add(
            AuditEvent(
                event_type="policy_deploy",
                target=f"{policy_name}:{hostname}",
                severity=(
                    "info"
                    if binding.application_status == POLICY_DEPLOY_APPLIED
                    else "warning"
                ),
                message=f"policy deployment {binding.application_status}",
                event_details={
                    "binding_id": binding.id,
                    "executor": binding.executor,
                    **details,
                },
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
        detail = (stderr.strip() or stdout.strip() or f"Ansible returned {rc}")[-4000:]
        raise RuntimeError(detail)

    @staticmethod
    def _require_keylime_success(result: dict[str, Any]) -> None:
        if result.get("rc") == 0:
            return
        raise RuntimeError(str(result.get("stderr") or result.get("stdout") or "Keylime failed"))

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
