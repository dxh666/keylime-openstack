from keylime_openstack.api.router import _dynamic_policy_audit_details
from keylime_openstack.models import PolicyBinding, TrustPolicy
from keylime_openstack.services.policy_deployment import (
    _opentcsm_dynamic_failure_details,
)


def test_opentcsm_dynamic_auth_rejection_is_productized() -> None:
    details = _opentcsm_dynamic_failure_details(
        "update_dmeasure_policy_syscall_table rc=152; stdout=[Error] ret:0x00000098"
    )

    assert details["policy_apply_authorization_status"] == "rejected"
    assert details["policy_apply_error_code"] == "TPCM_AUTH_REJECTED"
    assert "TPCM" in details["policy_apply_error_summary"]


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
    assert details["operation"] == "策略生效"
    assert details["result"] == "已入队"
    assert details["target_nodes"] == ["hygon23"]
