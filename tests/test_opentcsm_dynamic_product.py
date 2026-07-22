from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from keylime_openstack.api.router import (
    _dynamic_policy_audit_details,
    _tpcm_dynamic_global_control,
    set_tpcm_dynamic_global_switch,
)
from keylime_openstack.database import Base
from keylime_openstack.models import PolicyBinding, TrustPolicy
from keylime_openstack.schemas import TpcmDynamicGlobalSwitchIn
from keylime_openstack.services.policy_deployment import (
    PolicyDeploymentService,
    _opentcsm_dynamic_failure_details,
)


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

    PolicyDeploymentService._failed(
        binding,
        "raw ansible traceback that should stay out of policy lists",
        details,
    )

    assert binding.last_error == details["policy_apply_error_summary"]
    assert "raw ansible traceback" not in binding.last_error
    assert binding.binding_details["policy_apply_error_code"] == "TPCM_COMMAND_NOT_FOUND"


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
