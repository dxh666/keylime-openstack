from __future__ import annotations

from datetime import datetime, timezone

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
    PROVIDER_OPENTCSM,
    REGISTRATION_CONFLICT,
    REGISTRATION_VERIFIED,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_AGENT_UNMANAGED,
    TRUST_ROOT_TPCM,
    TRUST_ROOT_TPM,
)
from keylime_openstack.models import ComputeNode, EvidenceRecord, TrustedNodeProfile
from keylime_openstack.services.auth import authenticate_admin
from keylime_openstack.services.keylime import (
    normalize_agent_inventory_payload,
    parse_tenant_reglist_stdout,
)
from keylime_openstack.services.opentcsm_collect import sync_tpcm_profile
from keylime_openstack.services.trust_agents import (
    node_trust_agent_type,
    node_trust_managed,
    node_trusted_root_type,
)
from keylime_openstack.services.trust_registration import (
    legacy_trusted_node_profile_payload,
    upsert_trusted_node_profile,
)
from keylime_openstack.services.trust_registration_sync import sync_trusted_node_registrations

KEYLIME_DISCOVERY_METHOD = (
    "keylime_openstack.services.trust_registration_sync.KeylimeClient.list_registered_agents"
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


def test_keylime_agent_inventory_payload_normalizes_common_shapes() -> None:
    agents = normalize_agent_inventory_payload(
        {
            "results": {
                "agents": {
                    "22222222-2222-4222-8222-000000000008": {
                        "ip": "10.0.0.11",
                        "port": 9002,
                        "metadata": {"hostname": "compute-a"},
                    }
                }
            }
        },
        source="verifier-api",
    )

    assert agents == [
        {
            "agent_uuid": "22222222-2222-4222-8222-000000000008",
            "ip": "10.0.0.11",
            "port": 9002,
            "hostname": "compute-a",
            "metadata": {"hostname": "compute-a"},
            "source": "verifier-api",
        }
    ]


def test_keylime_tenant_reglist_text_parser_keeps_uuid_ip_pair() -> None:
    agents = parse_tenant_reglist_stdout(
        "agent 11111111-1111-4111-8111-000000000009\n"
        "contact_ip: 10.0.0.12\n"
    )

    assert agents[0]["agent_uuid"] == "11111111-1111-4111-8111-000000000009"
    assert agents[0]["ip"] == "10.0.0.12"


def test_registration_sync_keeps_env_maps_as_compatibility_input() -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="compute-f",
            hypervisor_name="nova-f",
            management_ip="10.0.0.16",
            role="compute",
        )
        memory_session.add(node)
        memory_session.flush()

        result = sync_trusted_node_registrations(
            memory_session,
            Settings(
                keylime_agent_hosts="compute-f",
                keylime_agent_ip_map="compute-f=10.0.0.16",
                keylime_agent_uuid_map="compute-f=33333333-3333-4333-8333-000000000016",
            ),
            discover_keylime=False,
        )

    assert result["static_keylime_registrations"][0]["node"] == "compute-f"
    assert node.keylime_agent_uuid == "33333333-3333-4333-8333-000000000016"
    assert node.trust_profile.trust_managed is True
    assert node.trust_profile.trusted_root_type == TRUST_ROOT_TPM
    assert node.trust_profile.adapter_type == ADAPTER_KEYLIME


def test_registration_sync_auto_binds_discovered_keylime_agent(monkeypatch) -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="compute-g",
            hypervisor_name="nova-g",
            management_ip="10.0.0.17",
            role="compute",
        )
        memory_session.add(node)
        memory_session.flush()

        monkeypatch.setattr(
            KEYLIME_DISCOVERY_METHOD,
            lambda *_args, **_kwargs: [
                {
                    "agent_uuid": "44444444-4444-4444-8444-000000000017",
                    "ip": "10.0.0.17",
                    "port": 9002,
                    "hostname": "",
                    "metadata": {},
                    "source": "verifier-api",
                }
            ],
        )
        result = sync_trusted_node_registrations(memory_session, Settings())

    assert result["keylime_agents_matched"][0]["node"] == "compute-g"
    assert node.keylime_agent_uuid == "44444444-4444-4444-8444-000000000017"
    assert node.trust_profile.trust_managed is True
    assert node.trust_profile.trusted_root_type == TRUST_ROOT_TPM


def test_registration_sync_does_not_turn_tpcm_unmanaged_node_into_keylime(monkeypatch) -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="compute-h",
            hypervisor_name="nova-h",
            management_ip="10.0.0.18",
            role="compute",
            facts={"trusted_root_type": "tpcm", "trusted_root": "Hygon TPCM"},
        )
        memory_session.add(node)
        memory_session.flush()

        monkeypatch.setattr(
            KEYLIME_DISCOVERY_METHOD,
            lambda *_args, **_kwargs: [
                {
                    "agent_uuid": "55555555-5555-4555-8555-000000000018",
                    "ip": "10.0.0.18",
                    "port": 9002,
                    "hostname": "",
                    "metadata": {},
                    "source": "verifier-api",
                }
            ],
        )
        result = sync_trusted_node_registrations(memory_session, Settings())

    assert result["registration_conflicts"][0]["node"] == "compute-h"
    assert node.keylime_agent_uuid == ""
    assert node.trust_profile.trust_managed is False
    assert node.trust_profile.trusted_root_type == TRUST_ROOT_TPCM
    assert node.trust_profile.registration_status == REGISTRATION_CONFLICT


def test_registration_sync_promotes_opentcsm_inventory_to_managed_tpcm() -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="compute-i",
            hypervisor_name="nova-i",
            management_ip="10.0.0.19",
            role="compute",
            facts={
                "trusted_root_type": "tpcm",
                "trusted_root": "Hygon TPCM",
                "trust_agent_name": "OpenTCSM",
                "tpcm_id": "tpcm-i",
            },
        )
        memory_session.add(node)
        memory_session.flush()

        result = sync_trusted_node_registrations(
            memory_session,
            Settings(keylime_auto_registration_enabled=False),
        )

    assert result["tpcm_registrations"][0]["node"] == "compute-i"
    assert node.trust_profile.trust_managed is True
    assert node.trust_profile.trusted_root_type == TRUST_ROOT_TPCM
    assert node.trust_profile.adapter_type == ADAPTER_OPENTCSM
    assert node.trust_profile.agent_identity["tpcm_id"] == "tpcm-i"
    assert node.trust_profile.capabilities[CAPABILITY_TPCM_DYNAMIC_MEASUREMENT] is True


def test_registration_sync_keeps_tpcm_root_without_agent_unmanaged() -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="compute-j",
            hypervisor_name="nova-j",
            management_ip="10.0.0.20",
            role="compute",
            facts={"trusted_root_type": "tpcm", "trusted_root": "Hygon TPCM"},
        )
        memory_session.add(node)
        memory_session.flush()

        result = sync_trusted_node_registrations(
            memory_session,
            Settings(keylime_auto_registration_enabled=False),
        )

    assert result["tpcm_registrations"] == []
    assert node.trust_profile.trust_managed is False
    assert node.trust_profile.trusted_root_type == TRUST_ROOT_TPCM
    assert node.trust_profile.capabilities == {}


def test_registration_sync_uses_opentcsm_evidence_as_tpcm_registration() -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="compute-k",
            hypervisor_name="nova-k",
            management_ip="10.0.0.21",
            role="compute",
            facts={"trusted_root_type": "tpcm", "trusted_root": "Hygon TPCM"},
        )
        memory_session.add(node)
        memory_session.flush()
        memory_session.add(
            EvidenceRecord(
                node_id=node.id,
                provider=PROVIDER_OPENTCSM,
                evidence_type="runtime",
                collected_at=datetime.now(timezone.utc),
                status="pass",
                summary="TPCM dynamic measurement pass",
                payload={
                    "trusted": True,
                    "raw": {
                        "tpcm_id": "tpcm-k",
                        "boot_measure_on": True,
                        "boot_status": "pass",
                        "boot_records": ["BIOS/U-BOOT", "shim.efi"],
                        "boot_measure_ref_number": 2,
                        "dynamic_measure_on": True,
                        "dynamic_measurement_status": "pass",
                        "dmeasure_policy": [{"object": "kernel_section"}],
                    },
                },
            )
        )
        memory_session.flush()

        result = sync_trusted_node_registrations(
            memory_session,
            Settings(keylime_auto_registration_enabled=False),
        )

    assert result["tpcm_registrations"][0]["node"] == "compute-k"
    assert node.trust_profile.trust_managed is True
    assert node.trust_profile.registration_status == REGISTRATION_VERIFIED
    assert node.trust_profile.agent_identity["tpcm_id"] == "tpcm-k"
    assert node.trust_profile.last_evidence_summary["boot_measurement_summary"]["records_preview"] == [
        "BIOS/U-BOOT",
        "shim.efi",
    ]


def test_opentcsm_evidence_ingestion_promotes_unmanaged_tpcm_profile() -> None:
    with _memory_session() as memory_session:
        node = ComputeNode(
            hostname="compute-l",
            hypervisor_name="nova-l",
            management_ip="10.0.0.22",
            role="compute",
            facts={"trusted_root_type": "tpcm", "trusted_root": "Hygon TPCM"},
        )
        memory_session.add(node)
        memory_session.flush()

        sync_tpcm_profile(
            memory_session,
            Settings(),
            node,
            {
                "trusted": True,
                "boot_status": "pass",
                "dynamic_measurement_status": "pass",
                "raw": {
                    "tpcm_id": "tpcm-l",
                    "boot_measure_on": True,
                    "dynamic_measure_on": True,
                    "boot_records": ["BIOS/U-BOOT"],
                    "boot_measure_ref_number": 1,
                    "dmeasure_policy": [{"object": "kernel_section"}],
                    "dmeasure_times": 7,
                },
            },
        )

    assert node.trust_profile.trust_managed is True
    assert node.trust_profile.adapter_type == ADAPTER_OPENTCSM
    assert node.trust_profile.agent_identity["registration_source"] == "opentcsm-evidence"
    assert node.trust_profile.last_evidence_summary["trusted"] is True


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine, future=True)
