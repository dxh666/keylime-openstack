from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from keylime_openstack.config import Settings
from keylime_openstack.constants import POLICY_IMA_RUNTIME
from keylime_openstack.models import ComputeNode, TrustPolicy
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.keylime_policy_deployment import KeylimePolicyDeployment
from keylime_openstack.services.policy_deployment import PolicyDeploymentService


def _client(tmp_path) -> KeylimeClient:
    settings = SimpleNamespace(
        temp_dir=str(tmp_path),
        keylime_tenant_service="keylime-tenant",
        keylime_verifier_url="https://172.31.100.10:8881",
        keylime_registrar_url="https://172.31.100.10:8891",
    )
    return KeylimeClient(settings)


def test_policy_update_is_followed_by_reactivation(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    commands: list[list[str]] = []

    def run(command: list[str]) -> dict[str, object]:
        commands.append(command)
        return {"rc": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(client, "_run_tenant_tool", run)

    result = client.tenant_tool_apply_policy(
        agent_uuid="33333333-3333-4333-8333-000000000022",
        agent_ip="172.31.100.22",
        runtime_policy_name="ima-content-hash",
        measured_boot_policy_name="mb-content-hash",
    )

    assert result["rc"] == 0
    assert commands[0][commands[0].index("-c") + 1] == "update"
    assert "--runtime-policy-name" in commands[0]
    assert "--mb-policy-name" in commands[0]
    assert commands[1][commands[1].index("-c") + 1] == "reactivate"


def test_missing_verifier_enrollment_falls_back_to_add(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path)
    operations: list[str] = []

    def run(command: list[str]) -> dict[str, object]:
        operation = command[command.index("-c") + 1]
        operations.append(operation)
        if operation == "update":
            return {"rc": 1, "stdout": "", "stderr": "agent not found"}
        return {"rc": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(client, "_run_tenant_tool", run)

    result = client.tenant_tool_apply_policy(
        agent_uuid="33333333-3333-4333-8333-000000000022",
        agent_ip="172.31.100.22",
        measured_boot_policy_name="mb-content-hash",
    )

    assert result["rc"] == 0
    assert operations == ["update", "add", "reactivate"]


def test_keylime_failure_includes_command_context() -> None:
    result = {
        "rc": 2,
        "stdout": "INFO:keylime.config:Reading configuration",
        "stderr": "",
        "command": ["docker", "compose", "run", "--rm", "keylime-tenant", "-c", "add"],
    }

    try:
        PolicyDeploymentService._require_keylime_success(result)
    except RuntimeError as exc:
        message = str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected keylime failure")

    assert "rc=2" in message
    assert "keylime-tenant -c add" in message
    assert "INFO:keylime.config" in message


def test_keylime_deployment_applies_ima_runtime_policy(tmp_path) -> None:
    class FakeAnsible:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def run(
            self,
            *,
            playbook: str,
            node: ComputeNode,
            workspace: Path,
            extra_vars: dict[str, Any],
        ) -> SimpleNamespace:
            self.calls.append(
                {
                    "playbook": playbook,
                    "node": node.hostname,
                    "workspace": workspace,
                    "extra_vars": extra_vars,
                }
            )
            Path(extra_vars["evidence_output_path"]).write_text(
                "10 abc ima-ng sha256:111 /usr/bin/python\n"
                "10 def ima-ng sha256:222 /usr/bin/bash\n",
                encoding="utf-8",
            )
            return SimpleNamespace(rc=0, stdout="ok", stderr="")

    class FakeKeylime:
        def __init__(self) -> None:
            self.created_measurements = ""
            self.created_excludes: list[str] = []
            self.stored_name = ""
            self.stored_policy: dict[str, Any] = {}
            self.applied_args: dict[str, Any] = {}

        def tenant_tool_create_runtime_policy(
            self,
            measurements: str,
            excludes: list[str],
        ) -> dict[str, Any]:
            self.created_measurements = measurements
            self.created_excludes = excludes
            return {"ima": {"ignored_keyrings": []}, "excludes": excludes}

        def tenant_tool_store_runtime_policy(
            self,
            *,
            name: str,
            runtime_policy: dict[str, Any],
        ) -> None:
            self.stored_name = name
            self.stored_policy = runtime_policy

        def tenant_tool_apply_policy(self, **kwargs: Any) -> dict[str, Any]:
            self.applied_args = kwargs
            return {"rc": 0, "stdout": "ok", "stderr": ""}

    fake_ansible = FakeAnsible()
    fake_keylime = FakeKeylime()

    @contextmanager
    def workspace() -> Iterator[Path]:
        yield tmp_path

    deployment = KeylimePolicyDeployment(
        settings=Settings(keylime_docker_dir=".", temp_dir=str(tmp_path)),
        ansible=fake_ansible,  # type: ignore[arg-type]
        keylime=fake_keylime,  # type: ignore[arg-type]
        workspace_factory=workspace,
        require_ansible_success=lambda rc, stdout, stderr: None,
        require_keylime_success=lambda result: None,
        active_external_policy_name=lambda node_id, policy_type: "",
        active_boot_policy_adapter=lambda node_id: {
            "measured_boot_policy_name": "mb-active",
        },
    )
    policy = TrustPolicy(
        id=8,
        name="runtime-baseline",
        policy_type=POLICY_IMA_RUNTIME,
        content={"node_ima_policy": "allow"},
        excludes=["/tmp"],
    )
    node = ComputeNode(
        id=9,
        hostname="csri9",
        management_ip="172.31.100.9",
        keylime_agent_uuid="uuid-csri9",
        keylime_agent_port=9002,
    )

    result = deployment.deploy_ima_runtime(policy, node)

    assert fake_ansible.calls[0]["playbook"] == "apply-ima-policy.yml"
    assert fake_keylime.created_excludes == ["/tmp"]
    assert fake_keylime.stored_name == result.external_name
    assert fake_keylime.stored_policy == result.rendered_policy
    assert result.external_name.startswith("klos-ima-runtime-baseline-csri9-")
    assert result.deployment_details["keylime_artifact"] == "runtime_policy"
    assert result.deployment_details["measurement_count"] == 2
    assert result.response["measurement_count"] == 2
    assert fake_keylime.applied_args["agent_uuid"] == "uuid-csri9"
    assert fake_keylime.applied_args["agent_ip"] == "172.31.100.9"
    assert fake_keylime.applied_args["runtime_policy_name"] == result.external_name
    assert fake_keylime.applied_args["measured_boot_policy_name"] == "mb-active"
