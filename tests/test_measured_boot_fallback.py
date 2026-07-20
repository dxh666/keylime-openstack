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
