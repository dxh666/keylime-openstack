import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import keylime_openstack.services.tpcm_dynamic_deployment as tpcm_dynamic_deployment
from keylime_openstack.api.router import (
    _dynamic_policy_audit_details,
    _tpcm_dynamic_global_control,
    set_tpcm_dynamic_global_switch,
)
from keylime_openstack.config import Settings
from keylime_openstack.database import Base
from keylime_openstack.models import AuditEvent, ComputeNode, PolicyBinding, TrustPolicy
from keylime_openstack.schemas import TpcmDynamicGlobalSwitchIn
from keylime_openstack.services.policy import _binding_last_error_for_output
from keylime_openstack.services.policy_deployment_audit import PolicyDeploymentAuditRecorder
from keylime_openstack.services.policy_deployment import (
    _opentcsm_dynamic_failure_details,
)
from keylime_openstack.services.policy_deployment_state import PolicyDeploymentState
from keylime_openstack.services.tpcm_dynamic_deployment import TpcmDynamicDeployment


def test_opentcsm_dynamic_auth_rejection_is_productized() -> None:
    details = _opentcsm_dynamic_failure_details(
        "update_dmeasure_policy_syscall_table rc=152; stdout=[Error] ret:0x00000098"
    )

    assert details["policy_apply_authorization_status"] == "rejected"
    assert details["policy_apply_error_code"] == "TPCM_AUTH_REJECTED"
    assert "TPCM" in details["policy_apply_error_summary"]


def test_opentcsm_dynamic_missing_command_is_productized() -> None:
    details = _opentcsm_dynamic_failure_details(
        "get_dmeasure_policy_before rc=127; stderr=command not found: get_dmeasure_policy"
    )

    assert details["policy_apply_error_code"] == "TPCM_COMMAND_NOT_FOUND"
    assert "OpenTCSM" in details["policy_apply_error_summary"]
    assert "get_dmeasure_policy" in details["policy_apply_error_summary"]


def test_failed_policy_binding_uses_product_summary_as_last_error() -> None:
    binding = PolicyBinding(binding_details={})
    details = _opentcsm_dynamic_failure_details(
        "FileNotFoundError: [Errno 2] No such file or directory: 'get_dmeasure_policy'"
    )

    PolicyDeploymentState.mark_failed(
        binding,
        "raw ansible traceback that should stay out of policy lists",
        details,
    )

    assert binding.last_error == details["policy_apply_error_summary"]
    assert "raw ansible traceback" not in binding.last_error
    assert binding.binding_details["policy_apply_error_code"] == "TPCM_COMMAND_NOT_FOUND"


def test_policy_output_prefers_dynamic_failure_summary_for_existing_rows() -> None:
    policy = TrustPolicy(
        name="hygon22 dynamic",
        policy_type="tpcm_dynamic_measurement",
        status="active",
    )
    binding = PolicyBinding(
        application_status="failed",
        last_error="raw ansible output with traceback",
        binding_details={
            "policy_apply_error_summary": "OpenTCSM 动态度量命令不可用。",
        },
    )

    assert _binding_last_error_for_output(policy, binding) == "OpenTCSM 动态度量命令不可用。"


def test_dynamic_policy_audit_details_use_product_fields() -> None:
    policy = TrustPolicy(
        id=12,
        name="hygon23-dynamic",
        policy_type="tpcm_dynamic_measurement",
        status="active",
        content={
            "environment_object_configs": {
                "kernel_section": {"enabled": True, "interval_milli": 60000},
                "syscall_table": {"enabled": False, "interval_milli": 60000},
                "idt_table": {"enabled": True, "interval_milli": 30000},
            },
            "environment_interval_milli": 60000,
        },
    )
    policy.bindings = [
        PolicyBinding(active=True, target_id=23, binding_details={"hostname": "hygon23"})
    ]

    details = _dynamic_policy_audit_details(policy, "tpcm_dynamic_policy_apply_queued")

    assert details["log_type"] == "dynamic_measurement"
    assert details["subject_name"] == "TPCM"
    assert details["object_name"] == "kernel_section,idt_table"
    assert details["measurement_type"] == "策略生效"
    assert details["measurement_baseline"] == ""
    assert details["operation"] == "策略生效"
    assert details["result"] == "已入队"
    assert details["target_nodes"] == ["hygon23"]
    assert details["node_dynamic_measure_enabled"] is True


def test_dynamic_policy_audit_details_show_node_switch_disabled() -> None:
    policy = TrustPolicy(
        id=13,
        name="hygon23-dynamic-disabled",
        policy_type="tpcm_dynamic_measurement",
        status="active",
        content={
            "node_dynamic_measure_enabled": False,
            "environment_objects": ["kernel_section", "syscall_table"],
            "environment_object_configs": {
                "kernel_section": {"enabled": True, "interval_milli": 60000},
                "syscall_table": {"enabled": True, "interval_milli": 60000},
                "idt_table": {"enabled": False, "interval_milli": 60000},
            },
        },
    )
    policy.bindings = [
        PolicyBinding(active=True, target_id=23, binding_details={"hostname": "hygon23"})
    ]

    details = _dynamic_policy_audit_details(policy, "tpcm_dynamic_policy_save")

    assert details["log_type"] == "dynamic_measurement"
    assert details["object_name"] == "全部动态度量对象"
    assert details["operation"] == "策略保存"
    assert details["result"] == "成功"
    assert details["node_dynamic_measure_enabled"] is False


def test_policy_deployment_audit_recorder_keeps_dynamic_product_fields() -> None:
    with _memory_session() as session:
        binding = PolicyBinding(
            id=77,
            executor="ansible",
            application_status="failed",
        )
        recorder = PolicyDeploymentAuditRecorder(session)

        recorder.record_policy_deployment(
            policy_name="hygon23-dynamic",
            policy_type="tpcm_dynamic_measurement",
            hostname="hygon23",
            binding=binding,
            details={
                "policy_apply_error_code": "TPCM_AUTH_REJECTED",
                "policy_apply_error_summary": "TPCM authorization rejected",
                "rendered_policy_sha256": "rendered-sha256",
                "environment_object_configs": {
                    "kernel_section": {"enabled": True, "interval_milli": 60000},
                    "syscall_table": {"enabled": False, "interval_milli": 60000},
                },
            },
        )

        event = session.scalars(select(AuditEvent)).one()

    assert event.event_type == "tpcm_dynamic_policy_apply"
    assert event.target == "hygon23-dynamic:hygon23"
    assert event.severity == "warning"
    assert event.message == "TPCM authorization rejected"
    assert event.event_details["binding_id"] == 77
    assert event.event_details["log_type"] == "tpcm_authorization"
    assert event.event_details["object_name"] == "kernel_section"
    assert event.event_details["measurement_type"] == "授权检测"
    assert event.event_details["measurement_baseline"] == "rendered-sha256"
    assert event.event_details["hash"] == "rendered-sha256"


def test_tpcm_dynamic_deployment_returns_productized_result(monkeypatch, tmp_path: Path) -> None:
    class FakeAuth:
        ref = "unit-test"
        uid = "unit-uid"
        public_fingerprint = "public-sha256"

        def playbook_vars(self) -> dict[str, object]:
            return {"ref": self.ref, "uid": self.uid}

        def metadata(self) -> dict[str, object]:
            return {"ref": self.ref, "uid": self.uid, "public_key_sha256": self.public_fingerprint}

    class FakeAnsible:
        def run(self, *, extra_vars: dict[str, object], **_: object) -> SimpleNamespace:
            result_path = Path(str(extra_vars["result_output_path"]))
            result_path.write_text(
                json.dumps(
                    {
                        "ok": True,
                        "applied_at": "2026-07-22T00:00:00+00:00",
                        "observed_before": [],
                        "observed_after": [{"object": "kernel_section"}],
                    }
                ),
                encoding="utf-8",
            )
            return SimpleNamespace(rc=0, stdout="", stderr="")

    class FakeCollector:
        def __init__(self, *_: object):
            pass

        def collect(self, _: ComputeNode) -> dict[str, object]:
            return {
                "trust_root": "Hygon TPCM",
                "agent_name": "OpenTCSM",
                "trusted": True,
                "dynamic_measurement_status": "active",
                "raw": {
                    "dynamic_measure_on": True,
                    "dynamic_measure_ref_number": 3,
                    "dmeasure_policy": [
                        {"object": "kernel_section", "interval_milli": 60000},
                        {"object": "idt_table", "interval_milli": 30000},
                    ],
                    "trust_report_failures": {},
                    "trust_report_sha256": "trust-sha256",
                    "dmeasure_policy_sha256": "dmeasure-sha256",
                },
            }

    @contextmanager
    def workspace():
        yield tmp_path

    monkeypatch.setattr(tpcm_dynamic_deployment, "load_opentcsm_auth_material", lambda *_: FakeAuth())
    monkeypatch.setattr(tpcm_dynamic_deployment, "OpenTcsmCollector", FakeCollector)

    deployment = TpcmDynamicDeployment(
        session=object(),  # type: ignore[arg-type]
        settings=Settings(trust_agent_type_map="hygon23=opentcsm_tpcm"),
        ansible=FakeAnsible(),  # type: ignore[arg-type]
        workspace_factory=workspace,
        require_ansible_success=lambda *_: None,
    )
    policy = TrustPolicy(
        name="hygon23-dynamic",
        policy_type="tpcm_dynamic_measurement",
        content={
            "environment_object_configs": {
                "kernel_section": {"enabled": True, "interval_milli": 60000},
                "syscall_table": {"enabled": False, "interval_milli": 60000},
                "idt_table": {"enabled": True, "interval_milli": 30000},
            },
            "environment_interval_milli": 60000,
        },
    )
    node = ComputeNode(hostname="hygon23", facts={"trust_agent_type": "opentcsm_tpcm"})

    result = deployment.deploy(policy, node)

    assert result.external_name.startswith("klos-tpcm-dyn-hygon23-dynamic-hygon23-")
    assert result.deployment_details["keylime_artifact"] == "opentcsm_dynamic_measurement_policy"
    assert result.deployment_details["trust_agent_type"] == "opentcsm_tpcm"
    assert result.deployment_details["dynamic_measure_ref_number"] == 3
    assert result.response["dynamic_measure_on"] is True
    assert result.response["environment_objects"] == ["kernel_section", "idt_table"]
    assert result.rendered_policy["observed"]["dmeasure_policy_sha256"] == "dmeasure-sha256"


def test_tpcm_dynamic_global_control_defaults_to_enabled() -> None:
    with _memory_session() as session:
        status = _tpcm_dynamic_global_control(session)

    assert status["enabled"] is True
    assert status["source"] == "default"


def test_tpcm_dynamic_global_switch_does_not_change_node_policy() -> None:
    with _memory_session() as session:
        policy = TrustPolicy(
            name="hygon23 环境动态度量策略",
            policy_type="tpcm_dynamic_measurement",
            status="active",
            content={
                "node_dynamic_measure_enabled": True,
                "dynamic_measure_required": True,
                "environment_object_configs": {
                    "kernel_section": {"enabled": True, "interval_milli": 60000},
                    "syscall_table": {"enabled": True, "interval_milli": 60000},
                    "idt_table": {"enabled": True, "interval_milli": 60000},
                },
            },
        )
        session.add(policy)
        session.commit()

        result = set_tpcm_dynamic_global_switch(
            TpcmDynamicGlobalSwitchIn(enabled=False),
            session,
        )
        session.refresh(policy)
        status = _tpcm_dynamic_global_control(session)

    assert result["ok"] is True
    assert status["enabled"] is False
    assert policy.content["node_dynamic_measure_enabled"] is True
    assert policy.content["dynamic_measure_required"] is True


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine, future=True)
