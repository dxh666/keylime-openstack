from __future__ import annotations

import pytest
from fastapi import HTTPException

from keylime_openstack.schemas import TrustPolicyIn
from keylime_openstack.services.policy import canonical_policy_type, validated_policy_payload


def test_legacy_tpm_policy_type_maps_to_measured_boot() -> None:
    assert canonical_policy_type("tpm_pcr") == "measured_boot"


def test_dynamic_policy_alias_maps_to_tpcm_dynamic_measurement() -> None:
    assert canonical_policy_type("opentcsm_dynamic") == "tpcm_dynamic_measurement"


def test_measured_boot_defaults_to_pcr_zero_through_seven() -> None:
    policy = TrustPolicyIn(
        name="compute-measured-boot",
        policy_type="measured_boot",
        target_node_ids=[3, 1, 3],
        content={},
    )

    payload, node_ids, deploy_now = validated_policy_payload(policy)

    assert payload["content"]["pcrs"] == list(range(8))
    assert payload["content"]["fallback_pcrs"] == [7]
    assert payload["content"]["reference_state_mode"] == "collect_from_node"
    assert node_ids == [1, 3]
    assert deploy_now is True


def test_measured_boot_rejects_accept_all() -> None:
    policy = TrustPolicyIn(
        name="unsafe-measured-boot",
        policy_type="measured_boot",
        target_node_ids=[1],
        content={"policy_engine": "accept-all"},
    )

    with pytest.raises(HTTPException, match="accept-all"):
        validated_policy_payload(policy)


def test_ima_policy_requires_measure_rule() -> None:
    policy = TrustPolicyIn(
        name="invalid-ima",
        policy_type="ima_runtime",
        target_node_ids=[1],
        content={"node_ima_policy": "dont_measure fsmagic=0x9fa0"},
    )

    with pytest.raises(HTTPException, match="measure 规则"):
        validated_policy_payload(policy)


def test_tpcm_dynamic_policy_defaults_to_opentcsm_guard() -> None:
    policy = TrustPolicyIn(
        name="hygon-dynamic",
        policy_type="tpcm_dynamic_measurement",
        target_node_ids=[4],
        content={},
    )

    payload, node_ids, deploy_now = validated_policy_payload(policy)

    assert payload["content"]["trust_agent_type"] == "opentcsm_tpcm"
    assert payload["content"]["dynamic_measure_required"] is True
    assert payload["content"]["require_clean_trust_report"] is False
    assert payload["content"]["environment_object_configs"] == {
        "kernel_section": {"enabled": True, "interval_milli": 60000},
        "syscall_table": {"enabled": True, "interval_milli": 60000},
        "idt_table": {"enabled": True, "interval_milli": 60000},
    }
    assert payload["content"]["environment_objects"] == [
        "kernel_section",
        "syscall_table",
        "idt_table",
    ]
    assert payload["content"]["minimum_dynamic_baselines"] == 0
    assert payload["content"]["keylime_artifact"] == "opentcsm_dynamic_measurement_policy"
    assert node_ids == [4]
    assert deploy_now is True


def test_tpcm_dynamic_policy_accepts_fixed_object_configs() -> None:
    policy = TrustPolicyIn(
        name="hygon-dynamic",
        policy_type="tpcm_dynamic_measurement",
        target_node_ids=[4],
        content={
            "environment_object_configs": {
                "kernel_section": {"enabled": True, "interval_milli": 30000},
                "syscall_table": {"enabled": False, "interval_milli": 60000},
                "idt_table": {"enabled": True, "interval_milli": 120000},
            }
        },
    )

    payload, _, _ = validated_policy_payload(policy)

    assert payload["content"]["environment_objects"] == ["kernel_section", "idt_table"]
    assert payload["content"]["environment_object_configs"]["kernel_section"] == {
        "enabled": True,
        "interval_milli": 30000,
    }
    assert payload["content"]["environment_object_configs"]["syscall_table"] == {
        "enabled": False,
        "interval_milli": 60000,
    }


def test_tpcm_dynamic_policy_requires_single_target_node() -> None:
    policy = TrustPolicyIn(
        name="hygon-dynamic",
        policy_type="tpcm_dynamic_measurement",
        target_node_ids=[4, 5],
        content={},
    )

    with pytest.raises(HTTPException, match="单个节点"):
        validated_policy_payload(policy)


def test_evm_creation_is_disabled() -> None:
    policy = TrustPolicyIn(
        name="evm-later",
        policy_type="evm",
        target_node_ids=[1],
    )

    with pytest.raises(HTTPException, match="尚未启用"):
        validated_policy_payload(policy)
