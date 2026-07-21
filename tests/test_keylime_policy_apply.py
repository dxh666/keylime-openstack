from __future__ import annotations

from types import SimpleNamespace

from keylime_openstack.services.keylime import KeylimeClient
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
