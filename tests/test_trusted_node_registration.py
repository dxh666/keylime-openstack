from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.database import Base
from keylime_openstack.constants import (
    ADAPTER_KEYLIME,
    ADAPTER_OPENTCSM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_AGENT_UNMANAGED,
    TRUST_ROOT_TPCM,
    TRUST_ROOT_TPM,
)
from keylime_openstack.models import ComputeNode, TrustedNodeProfile
from keylime_openstack.services.auth import authenticate_admin
from keylime_openstack.services.trust_agents import (
    node_trust_agent_type,
    node_trust_managed,
    node_trusted_root_type,
)
from keylime_openstack.services.trust_registration import (
    legacy_trusted_node_profile_payload,
    upsert_trusted_node_profile,
)


def test_keylime_profile_payload_uses_inventory_data_not_hostname() -> None:
    node = ComputeNode(
        hostname="compute-a",
        hypervisor_name="nova-a",
        management_ip="10.0.0.11",
        role="compute",
        keylime_agent_uuid="agent-a",
        keylime_agent_ip="10.0.0.11",
        keylime_agent_port=9002,
        facts={"trusted_root": "TPM 2.0"},
    )
    settings = Settings(trust_agent_type_map="compute-a=keylime")

    payload = legacy_trusted_node_profile_payload(node, settings)

    assert payload["openstack_compute_name"] == "nova-a"
    assert payload["trusted_root_type"] == TRUST_ROOT_TPM
    assert payload["trust_managed"] is True
    assert payload["adapter_type"] == ADAPTER_KEYLIME
    assert payload["agent_identity"]["keylime_agent_uuid"] == "agent-a"
    assert payload["capabilities"][CAPABILITY_TRUSTED_BOOT] is True
    assert payload["capabilities"][CAPABILITY_IMA_RUNTIME] is True
    assert payload["capabilities"][CAPABILITY_TPCM_DYNAMIC_MEASUREMENT] is False


def test_unmanaged_tpcm_node_has_root_but_no_dynamic_capability() -> None:
    node = ComputeNode(
        hostname="compute-b",
        hypervisor_name="nova-b",
        management_ip="10.0.0.12",
        role="compute",
        facts={"trusted_root_type": "tpcm", "trusted_root": "Hygon TPCM"},
    )
    settings = Settings(trust_agent_type_map="compute-b=unmanaged")

    payload = legacy_trusted_node_profile_payload(node, settings)

    assert payload["trusted_root_type"] == TRUST_ROOT_TPCM
    assert payload["trust_managed"] is False
    assert payload["adapter_type"] == ""
    assert payload["capabilities"] == {}


def test_structured_profile_wins_over_legacy_agent_map() -> None:
    node = ComputeNode(
        hostname="compute-c",
        hypervisor_name="nova-c",
        management_ip="10.0.0.13",
        role="compute",
        facts={"trust_agent_type": "keylime", "trusted_root": "TPM 2.0"},
    )
    node.trust_profile = TrustedNodeProfile(
        hostname="compute-c",
        openstack_compute_name="nova-c",
        management_ip="10.0.0.13",
        is_openstack_compute=True,
        trust_managed=True,
        trusted_root_type=TRUST_ROOT_TPCM,
        adapter_type=ADAPTER_OPENTCSM,
        capabilities={CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: True},
    )
    settings = Settings(trust_agent_type_map="compute-c=unmanaged")

    assert node_trust_agent_type(node, settings) == TRUST_AGENT_OPENTCSM_TPCM
    assert node_trust_managed(node, settings) is True
    assert node_trusted_root_type(node, settings) == TRUST_ROOT_TPCM


def test_structured_unmanaged_profile_blocks_legacy_keylime_uuid() -> None:
    node = ComputeNode(
        hostname="compute-d",
        hypervisor_name="nova-d",
        management_ip="10.0.0.14",
        role="compute",
        keylime_agent_uuid="stale-agent",
    )
    node.trust_profile = TrustedNodeProfile(
        hostname="compute-d",
        openstack_compute_name="nova-d",
        management_ip="10.0.0.14",
        is_openstack_compute=True,
        trust_managed=False,
        trusted_root_type=TRUST_ROOT_TPCM,
        adapter_type="",
        capabilities={},
    )

    assert node_trust_agent_type(node, Settings()) == TRUST_AGENT_UNMANAGED
    assert node_trust_managed(node, Settings()) is False


def test_admin_password_prefers_dedicated_password_and_falls_back_to_token() -> None:
    settings = Settings(admin_username="ops", admin_password="secret", admin_token="token")
    assert authenticate_admin(settings, "ops", "secret") is True
    assert authenticate_admin(settings, "ops", "token") is False

    token_only = Settings(admin_username="admin", admin_password="", admin_token="token")
    assert authenticate_admin(token_only, "admin", "token") is True


def test_registration_upsert_infers_tpcm_adapter_and_capabilities() -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="compute-e",
            hypervisor_name="nova-e",
            management_ip="10.0.0.15",
            role="compute",
        )
        memory_session.add(node)
        memory_session.flush()

        profile = upsert_trusted_node_profile(
            memory_session,
            node,
            Settings(),
            {
                "trust_managed": True,
                "trusted_root_type": "tpcm",
                "agent_identity": {"tpcm_id": "tpcm-id"},
            },
        )

    assert profile.adapter_type == ADAPTER_OPENTCSM
    assert profile.capabilities[CAPABILITY_TRUSTED_BOOT] is True
    assert profile.capabilities[CAPABILITY_TPCM_DYNAMIC_MEASUREMENT] is True
    assert profile.agent_identity["tpcm_id"] == "tpcm-id"


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine, future=True)
