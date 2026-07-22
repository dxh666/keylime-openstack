from datetime import datetime, timezone
from types import SimpleNamespace

from keylime_openstack.constants import (
    PROVIDER_OPENTCSM,
    TRUST_AGENT_KEYLIME,
    TRUST_AGENT_UNMANAGED,
)
from keylime_openstack.services.keylime_gate import _external_report_summary, _remediation


def test_missing_verifier_enrollment_points_to_trusted_boot_redeploy():
    remediation = _remediation(
        event_id="keylime-verifier-agent-not-found",
        trusted=False,
        host="csri8",
        trust_agent_type=TRUST_AGENT_KEYLIME,
    )

    commands = "\n".join(remediation["next_commands"])
    assert remediation["category"] == "verifier-enrollment"
    assert "可信启动策略页面" in commands
    assert "keylime-ima-runtime-policy-refresh.sh" not in commands


def test_unmanaged_remediation_uses_product_language():
    remediation = _remediation(
        event_id="TRUST_AGENT_UNMANAGED",
        trusted=False,
        host="hygon22",
        trust_agent_type=TRUST_AGENT_UNMANAGED,
    )

    assert remediation["category"] == "inventory"
    assert remediation["summary"] == "计算节点未纳入可信代理纳管。"
    assert "TRUST_AGENT_TYPE_MAP" in "\n".join(remediation["next_commands"])


def test_ima_missing_remediation_uses_product_language():
    remediation = _remediation(
        event_id="WAITING_FOR_IMA_MISSING",
        trusted=False,
        host="csri8",
        trust_agent_type=TRUST_AGENT_KEYLIME,
    )

    assert remediation["category"] == "ima-runtime-policy"
    assert remediation["summary"].startswith("Keylime verifier 中未绑定该节点的 IMA 运行时策略")


def test_opentcsm_report_summary_exposes_product_fields_without_command_output():
    collected_at = datetime(2026, 7, 21, 7, 0, tzinfo=timezone.utc)
    record = SimpleNamespace(
        provider=PROVIDER_OPENTCSM,
        collected_at=collected_at,
        valid_until=None,
        status="pass",
        summary="TPCM trusted boot pass",
        payload={
            "agent_name": "OpenTCSM",
            "trust_root": "Hygon TPCM",
            "report_type": "tpcm",
            "trusted": True,
            "errors": [],
            "raw": {
                "tpcm_id": "E9FA9758029E6318B21093811A51D4EC",
                "trust_status": "trusted",
                "boot_measure_on": True,
                "dynamic_measure_on": True,
                "trust_report_eval": 100,
                "trust_report_failures": {},
                "boot_records": ["BIOS/U-BOOT", "/EFI/anolis/shim.efi"],
                "dmeasure_policy": [
                    {"object": "kernel_section", "interval_milli": 60000},
                    {"object": "syscall_table", "interval_milli": 60000},
                ],
                "trust_report_sha256": "a" * 64,
                "commands": {"trust_report": {"stdout": "large output"}},
            },
        },
    )

    summary = _external_report_summary(record)

    assert summary["trusted"] is True
    assert summary["tpcm_id"] == "E9FA9758029E6318B21093811A51D4EC"
    assert summary["boot_measure_on"] is True
    assert summary["dynamic_measure_on"] is True
    assert summary["failure_count"] == 0
    assert summary["boot_record_count"] == 2
    assert summary["dmeasure_policy"] == [
        {"object": "kernel_section", "interval_milli": 60000},
        {"object": "syscall_table", "interval_milli": 60000},
    ]
    assert summary["trust_report_sha256"] == "a" * 64
    assert "commands" not in summary
