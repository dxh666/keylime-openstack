from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    TRUST_ROOT_TPCM,
    TRUST_ROOT_TPM,
)
from keylime_openstack.database import Base
from keylime_openstack.models import ComputeNode, TrustPolicy
from keylime_openstack.schemas import TrustPolicyIn
from keylime_openstack.services.policy import (
    bind_policy_to_nodes,
    canonical_policy_type,
    validated_policy_payload,
)
from keylime_openstack.services.trust_registration import upsert_trusted_node_profile


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


def test_tpcm_measured_boot_uses_tpcm_baseline_contract() -> None:
    policy = TrustPolicyIn(
        name="hygon-tpcm-boot",
        policy_type="measured_boot",
        target_node_ids=[4],
        content={"trusted_root_type": "tpcm"},
    )

    payload, node_ids, deploy_now = validated_policy_payload(policy)

    assert payload["content"]["trusted_root_type"] == TRUST_ROOT_TPCM
    assert payload["content"]["policy_scope"] == "trusted_boot"
    assert payload["content"]["evidence_type"] == "tpcm_boot_measurement"
    assert payload["content"]["baseline_generation"] == "auto_collect_tpcm_boot_measurement"
    assert payload["content"]["boot_measure_required"] is True
    assert payload["content"]["minimum_boot_references"] == 1
    assert payload["content"]["require_clean_trust_report"] is True
    assert payload["content"]["tpcm_apply_mode"] == "management_baseline"
    assert payload["content"]["tpcm_write_enabled"] is False
    assert payload["content"]["keylime_artifact"] == "opentcsm_tpcm_boot_policy"
    assert "pcrs" not in payload["content"]
    assert node_ids == [4]
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


def test_tpcm_measured_boot_bind_rejects_unmanaged_tpcm_target() -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="hygon22",
            hypervisor_name="hygon22",
            management_ip="172.31.100.22",
            role="compute",
        )
        memory_session.add(node)
        memory_session.flush()
        upsert_trusted_node_profile(
            memory_session,
            node,
            Settings(),
            {
                "trust_managed": False,
                "trusted_root_type": TRUST_ROOT_TPCM,
                "capabilities": {},
            },
        )
        payload, node_ids, _ = validated_policy_payload(
            TrustPolicyIn(
                name="hygon22-boot",
                policy_type="measured_boot",
                target_node_ids=[node.id],
                content={"trusted_root_type": "tpcm"},
            )
        )
        policy = TrustPolicy(**payload)
        memory_session.add(policy)
        memory_session.flush()

        with pytest.raises(HTTPException, match="已纳管"):
            bind_policy_to_nodes(
                memory_session,
                policy,
                node_ids,
                deploy_now=True,
                settings=Settings(),
            )


def test_tpm_measured_boot_bind_rejects_tpcm_target() -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="hygon23",
            hypervisor_name="hygon23",
            management_ip="172.31.100.23",
            role="compute",
        )
        memory_session.add(node)
        memory_session.flush()
        upsert_trusted_node_profile(
            memory_session,
            node,
            Settings(),
            {
                "trust_managed": True,
                "trusted_root_type": TRUST_ROOT_TPCM,
                "capabilities": {
                    CAPABILITY_TRUSTED_BOOT: True,
                    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: True,
                },
            },
        )
        payload, node_ids, _ = validated_policy_payload(
            TrustPolicyIn(
                name="wrong-root-boot",
                policy_type="measured_boot",
                target_node_ids=[node.id],
                content={"trusted_root_type": TRUST_ROOT_TPM},
            )
        )
        policy = TrustPolicy(**payload)
        memory_session.add(policy)
        memory_session.flush()

        with pytest.raises(HTTPException, match="TPM"):
            bind_policy_to_nodes(
                memory_session,
                policy,
                node_ids,
                deploy_now=True,
                settings=Settings(),
            )


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
    assert payload["content"]["node_dynamic_measure_enabled"] is True
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
    assert payload["content"]["delete_unmanaged_objects"] is False
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
            },
            "delete_unmanaged_objects": True,
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
    assert payload["content"]["delete_unmanaged_objects"] is False


def test_tpcm_dynamic_policy_node_switch_keeps_object_configs() -> None:
    policy = TrustPolicyIn(
        name="hygon-dynamic-disabled",
        policy_type="tpcm_dynamic_measurement",
        target_node_ids=[4],
        content={
            "node_dynamic_measure_enabled": False,
            "environment_object_configs": {
                "kernel_section": {"enabled": True, "interval_milli": 30000},
                "syscall_table": {"enabled": True, "interval_milli": 60000},
                "idt_table": {"enabled": False, "interval_milli": 120000},
            },
        },
    )

    payload, _, _ = validated_policy_payload(policy)

    assert payload["content"]["node_dynamic_measure_enabled"] is False
    assert payload["content"]["dynamic_measure_required"] is False
    assert payload["content"]["environment_objects"] == []
    assert payload["content"]["environment_object_configs"]["kernel_section"] == {
        "enabled": True,
        "interval_milli": 30000,
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


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine, future=True)
