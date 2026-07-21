import json

from keylime_openstack.config import Settings
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.policy_deployment import (
    _parse_tpm2_pcrread_sha256,
    _tpm_policy_from_pcrs,
)


def test_parse_tpm2_pcrread_sha256_and_render_policy():
    output = """
  sha256:
    0 : 0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
    7 : 0xBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB
"""

    values = _parse_tpm2_pcrread_sha256(output, [0, 7])
    assert values == {
        0: "a" * 64,
        7: "b" * 64,
    }

    policy = _tpm_policy_from_pcrs(values)
    assert policy == {
        "0": ["a" * 64],
        "7": ["b" * 64],
        "mask": "0x81",
    }


def test_tenant_tool_apply_policy_strips_local_mask_from_tpm_policy():
    client = KeylimeClient(Settings(keylime_docker_dir="."))
    commands = []

    def fake_run(command):
        commands.append(command)
        return {"rc": 0, "stdout": "ok", "stderr": ""}

    client._run_tenant_tool = fake_run  # type: ignore[method-assign]

    client.tenant_tool_apply_policy(
        agent_uuid="11111111-1111-4111-8111-000000000009",
        tpm_policy={"7": ["c" * 64], "mask": "0x80"},
    )

    update_command = commands[0]
    tpm_policy_arg = update_command[update_command.index("--tpm_policy") + 1]
    assert json.loads(tpm_policy_arg) == {"7": ["c" * 64]}


def test_tenant_tool_replace_existing_deletes_before_add():
    client = KeylimeClient(Settings(keylime_docker_dir="."))
    commands = []

    def fake_run(command):
        commands.append(command)
        return {"rc": 0, "stdout": "ok", "stderr": ""}

    client._run_tenant_tool = fake_run  # type: ignore[method-assign]

    client.tenant_tool_apply_policy(
        agent_uuid="11111111-1111-4111-8111-000000000009",
        agent_ip="172.31.100.9",
        tpm_policy={"7": ["d" * 64], "mask": "0x80"},
        replace_existing=True,
    )

    assert commands[0][commands[0].index("-c") + 1] == "delete"
    assert commands[1][commands[1].index("-c") + 1] == "add"
    assert commands[2][commands[2].index("-c") + 1] == "reactivate"
    tpm_policy_arg = commands[1][commands[1].index("--tpm_policy") + 1]
    assert json.loads(tpm_policy_arg) == {"7": ["d" * 64]}


def test_tenant_tool_can_disable_measured_boot_default():
    client = KeylimeClient(Settings(keylime_docker_dir="."))
    commands = []

    def fake_run(command):
        commands.append(command)
        return {"rc": 0, "stdout": "ok", "stderr": ""}

    client._run_tenant_tool = fake_run  # type: ignore[method-assign]

    client.tenant_tool_apply_policy(
        agent_uuid="11111111-1111-4111-8111-000000000009",
        agent_ip="172.31.100.9",
        tpm_policy={"7": ["d" * 64], "mask": "0x80"},
        disable_measured_boot=True,
        replace_existing=True,
    )

    add_command = commands[1]
    assert "--mb-policy" in add_command
    assert add_command[add_command.index("--mb-policy") + 1].endswith(
        "/empty-measured-boot-policy.json"
    )
