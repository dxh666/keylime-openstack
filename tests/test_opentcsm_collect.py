from keylime_openstack.models import ComputeNode
from keylime_openstack.services.opentcsm_collect import normalize_opentcsm_collection


def _command(stdout: str, rc: int = 0) -> dict[str, object]:
    return {
        "command": "test",
        "rc": rc,
        "stdout": stdout,
        "stderr": "",
    }


def test_trusted_tpcm_report_eval_is_not_a_failure_counter() -> None:
    node = ComputeNode(hostname="hygon23", management_ip="172.31.100.23")
    report = normalize_opentcsm_collection(
        node,
        {
            "hostname": "hygon23",
            "trust_root": "Hygon TPCM",
            "agent_name": "OpenTCSM",
            "commands": {
                "trust_status": _command("Trust status: trusted\n"),
                "global_control_policy": _command(
                    "policy->boot_measure_on: ON\n"
                    "policy->dynamic_measure_on: ON\n"
                ),
                "boot_measure_records": _command(
                    "[0].name: BIOS/U-BOOT\n"
                    "[1].name: /EFI/anolis/shim.efi\n"
                ),
                "tpcm_info": _command("dmeasure_times: 22\n"),
                "dmeasure_policy": _command(
                    "item index: 0\n"
                    "[0].be_type: 0\n"
                    "[0].be_interval_milli: 60000\n"
                    "[0].object: kernel_section\n\n"
                    "item index: 1\n"
                    "[1].be_type: 0\n"
                    "[1].be_interval_milli: 60000\n"
                    "[1].object: syscall_table\n\n"
                    "item index: 2\n"
                    "[2].be_type: 0\n"
                    "[2].be_interval_milli: 60000\n"
                    "[2].object: idt_table\n"
                ),
                "trust_report": _command(
                    "policy->be_boot_measure_on: ON\n"
                    "policy->be_dynamic_measure_on: ON\n"
                    "report.content.be_eval: 0x00000064\n"
                    "report.content.be_ilegal_program_load: 0x00000000\n"
                    "report.content.be_ilegal_lib_load: 0x00000000\n"
                    "report.content.be_ilegal_kernel_module_load: 0x00000000\n"
                    "report.content.be_ilegal_file_access: 0x00000000\n"
                    "report.content.be_ilegal_device_access: 0x00000000\n"
                    "report.content.be_ilegal_network_inreq: 0x00000000\n"
                    "report.content.be_ilegal_network_outreq: 0x00000000\n"
                    "report.content.be_process_code_measure_fail: 0x00000000\n"
                    "report.content.be_kernel_code_measure_fail: 0x00000000\n"
                    "report.content.be_kernel_data_measure_fail: 0x00000000\n"
                    "report.content.be_notify_fail: 0x00000000\n"
                ),
            },
        },
    )

    assert report["trusted"] is True
    assert report["boot_status"] == "pass"
    assert report["dynamic_measurement_status"] == "pass"
    assert report["raw"]["trust_report_eval"] == 100
    assert report["raw"]["trust_report_failures"] == {}
    assert report["raw"]["dmeasure_policy"] == [
        {"index": 0, "be_type": 0, "interval_milli": 60000, "object": "kernel_section"},
        {"index": 1, "be_type": 0, "interval_milli": 60000, "object": "syscall_table"},
        {"index": 2, "be_type": 0, "interval_milli": 60000, "object": "idt_table"},
    ]
