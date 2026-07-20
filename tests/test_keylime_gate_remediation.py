from keylime_openstack.constants import TRUST_AGENT_KEYLIME
from keylime_openstack.services.keylime_gate import _remediation


def test_missing_verifier_enrollment_points_to_trusted_boot_redeploy():
    remediation = _remediation(
        "keylime-verifier-agent-not-found",
        False,
        "csri8",
        TRUST_AGENT_KEYLIME,
    )

    commands = "\n".join(remediation["next_commands"])
    assert remediation["category"] == "verifier-enrollment"
    assert "可信启动策略页面" in commands
    assert "keylime-ima-runtime-policy-refresh.sh" not in commands
