"""Trusted-node registration profile helpers.

The product model is compute/non-compute, managed/unmanaged, trusted root type,
and capabilities. Keylime and OpenTCSM are adapter choices behind that model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    ADAPTER_KEYLIME,
    ADAPTER_OPENTCSM,
    CAPABILITY_EVM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    REGISTRATION_REGISTERED,
    REGISTRATION_UNMANAGED,
    REGISTRATION_VERIFIED,
    TRUST_AGENT_KEYLIME,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_AGENT_UNMANAGED,
    TRUST_ROOT_TPCM,
    TRUST_ROOT_TPM,
    TRUST_ROOT_UNKNOWN,
)
from keylime_openstack.models import ComputeNode, TrustedNodeProfile
from keylime_openstack.services.trust_agents import (
    node_trust_agent_type,
    node_trust_managed,
    node_trusted_root_type,
)

__all__ = [
    "ensure_trusted_node_profile",
    "legacy_trusted_node_profile_payload",
    "profile_manually_configured",
    "profile_payload",
    "registration_status_after_verification",
    "trusted_node_capable_of_tpcm_dynamic_measurement",
    "upsert_trusted_node_profile",
]


def ensure_trusted_node_profile(
    session: Session,
    node: ComputeNode,
    settings: Settings,
) -> TrustedNodeProfile:
    """Materialize the product profile for a node when it does not exist yet."""

    profile = getattr(node, "trust_profile", None)
    if profile:
        return _sync_node_cache(profile, node)

    payload = legacy_trusted_node_profile_payload(node, settings)
    profile = TrustedNodeProfile(node=node, node_id=node.id, **payload)
    session.add(profile)
    session.flush()
    return profile


def legacy_trusted_node_profile_payload(
    node: ComputeNode,
    settings: Settings,
) -> dict[str, Any]:
    """Build a registration profile from existing inventory fields.

    This is a migration bridge. It deliberately reads data from the existing
    model and configuration instead of checking specific lab hostnames.
    """

    agent_type = node_trust_agent_type(node, settings)
    trust_managed = node_trust_managed(node, settings)
    trusted_root_type = node_trusted_root_type(node, settings)
    facts = node.facts or {}
    adapter_type = _adapter_type(agent_type, trust_managed)
    capabilities = _capabilities(
        trusted_root_type=trusted_root_type,
        adapter_type=adapter_type,
        trust_managed=trust_managed,
    )
    return {
        "hostname": node.hostname,
        "openstack_compute_name": node.hypervisor_name or node.hostname,
        "management_ip": node.management_ip,
        "is_openstack_compute": node.role == "compute",
        "trust_managed": trust_managed,
        "trusted_root_type": trusted_root_type,
        "adapter_type": adapter_type,
        "agent_endpoint": _agent_endpoint(node, adapter_type),
        "agent_identity": _agent_identity(node, adapter_type, facts),
        "capabilities": capabilities,
        "registration_status": REGISTRATION_REGISTERED if trust_managed else REGISTRATION_UNMANAGED,
        "last_evidence_summary": {},
    }


def profile_payload(profile: TrustedNodeProfile, node: ComputeNode | None = None) -> dict[str, Any]:
    """Return the API-facing product profile payload."""

    hostname = profile.hostname or (node.hostname if node else "")
    return {
        "id": profile.id,
        "node_id": profile.node_id,
        "hostname": hostname,
        "openstack_compute_name": profile.openstack_compute_name
        or (node.hypervisor_name if node else "")
        or hostname,
        "management_ip": profile.management_ip or (node.management_ip if node else ""),
        "is_openstack_compute": profile.is_openstack_compute,
        "trust_managed": profile.trust_managed,
        "trusted_root_type": profile.trusted_root_type or TRUST_ROOT_UNKNOWN,
        "adapter_type": profile.adapter_type or "",
        "agent_endpoint": dict(profile.agent_endpoint or {}),
        "agent_identity": dict(profile.agent_identity or {}),
        "capabilities": dict(profile.capabilities or {}),
        "registration_status": profile.registration_status or REGISTRATION_UNMANAGED,
        "last_verified_at": profile.last_verified_at,
        "last_evidence_summary": dict(profile.last_evidence_summary or {}),
    }


def trusted_node_capable_of_tpcm_dynamic_measurement(profile: TrustedNodeProfile) -> bool:
    capabilities = dict(profile.capabilities or {})
    return bool(
        profile.is_openstack_compute
        and profile.trust_managed
        and profile.trusted_root_type == TRUST_ROOT_TPCM
        and capabilities.get(CAPABILITY_TPCM_DYNAMIC_MEASUREMENT) is True
    )


def upsert_trusted_node_profile(
    session: Session,
    node: ComputeNode,
    settings: Settings,
    registration: dict[str, Any],
) -> TrustedNodeProfile:
    profile = ensure_trusted_node_profile(session, node, settings)
    trust_managed = bool(registration.get("trust_managed"))
    trusted_root_type = _normalize_trusted_root_type(
        str(registration.get("trusted_root_type") or profile.trusted_root_type or "")
    )
    raw_adapter_type = str(registration.get("adapter_type") or "")
    if not raw_adapter_type and "trusted_root_type" not in registration:
        raw_adapter_type = str(profile.adapter_type or "")
    adapter_type = _normalize_adapter_type(
        raw_adapter_type,
        trusted_root_type=trusted_root_type,
        trust_managed=trust_managed,
    )
    capabilities = dict(registration.get("capabilities") or {})
    capabilities = _normalize_capabilities(
        capabilities,
        trusted_root_type=trusted_root_type,
        adapter_type=adapter_type,
        trust_managed=trust_managed,
    )
    profile.hostname = str(registration.get("hostname") or node.hostname)
    profile.openstack_compute_name = str(
        registration.get("openstack_compute_name")
        or node.hypervisor_name
        or profile.openstack_compute_name
        or node.hostname
    )
    profile.management_ip = str(
        registration.get("management_ip") or node.management_ip or profile.management_ip or ""
    )
    is_openstack_compute = registration.get("is_openstack_compute")
    profile.is_openstack_compute = (
        bool(is_openstack_compute) if is_openstack_compute is not None else node.role == "compute"
    )
    profile.trust_managed = trust_managed
    profile.trusted_root_type = trusted_root_type
    profile.adapter_type = adapter_type
    profile.agent_endpoint = dict(registration.get("agent_endpoint") or {}) or _agent_endpoint(
        node,
        adapter_type,
    )
    registration_source = str(
        registration.get("registration_source")
        or registration.get("configuration_source")
        or ""
    ).strip()
    agent_identity = dict(registration.get("agent_identity") or {}) or _agent_identity(
        node,
        adapter_type,
        node.facts or {},
    )
    if registration_source:
        agent_identity["registration_source"] = registration_source
    profile.agent_identity = agent_identity
    profile.capabilities = capabilities
    profile.registration_status = str(
        registration.get("registration_status")
        or (REGISTRATION_REGISTERED if trust_managed else REGISTRATION_UNMANAGED)
    )
    _sync_agent_cache_from_profile(node, settings, profile)
    profile.updated_at = datetime.now(timezone.utc)
    session.flush()
    return profile


def profile_manually_configured(profile: TrustedNodeProfile) -> bool:
    """Return True when a profile was saved from the product configuration UI/API."""

    identity = dict(profile.agent_identity or {})
    return str(identity.get("registration_source") or "").lower() == "manual"


def registration_status_after_verification(result: dict[str, Any]) -> str:
    if result.get("trusted") is True:
        return REGISTRATION_VERIFIED
    if result.get("status") == "collected" or result.get("ok") is True:
        return REGISTRATION_REGISTERED
    return REGISTRATION_UNMANAGED if result.get("status") == REGISTRATION_UNMANAGED else REGISTRATION_REGISTERED


def _sync_node_cache(profile: TrustedNodeProfile, node: ComputeNode) -> TrustedNodeProfile:
    changed = False
    values = {
        "hostname": node.hostname,
        "openstack_compute_name": node.hypervisor_name or node.hostname,
        "management_ip": node.management_ip,
        "is_openstack_compute": node.role == "compute",
    }
    for field, value in values.items():
        if value and getattr(profile, field) != value:
            setattr(profile, field, value)
            changed = True
    if changed:
        profile.updated_at = datetime.now(timezone.utc)
    return profile


def _sync_agent_cache_from_profile(
    node: ComputeNode,
    settings: Settings,
    profile: TrustedNodeProfile,
) -> None:
    """Keep legacy adapter fields aligned while verification still reads them."""

    if not profile.trust_managed or profile.adapter_type != ADAPTER_KEYLIME:
        return

    endpoint = dict(profile.agent_endpoint or {})
    identity = dict(profile.agent_identity or {})
    agent_uuid = str(identity.get("keylime_agent_uuid") or "").strip()
    agent_ip = str(endpoint.get("host") or "").strip()
    if agent_uuid:
        node.keylime_agent_uuid = agent_uuid
    if agent_ip:
        node.keylime_agent_ip = agent_ip
    try:
        agent_port = int(endpoint.get("port") or node.keylime_agent_port or settings.keylime_agent_port)
    except (TypeError, ValueError):
        agent_port = settings.keylime_agent_port
    node.keylime_agent_port = agent_port


def _adapter_type(agent_type: str, trust_managed: bool) -> str:
    if not trust_managed or agent_type == TRUST_AGENT_UNMANAGED:
        return ""
    if agent_type == TRUST_AGENT_KEYLIME:
        return ADAPTER_KEYLIME
    if agent_type == TRUST_AGENT_OPENTCSM_TPCM:
        return ADAPTER_OPENTCSM
    return agent_type


def _normalize_adapter_type(
    value: str,
    *,
    trusted_root_type: str,
    trust_managed: bool,
) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if not trust_managed:
        return ""
    if normalized in {"keylime", "keylime_agent"}:
        return ADAPTER_KEYLIME
    if normalized in {"opentcsm", "opentcsm_tpcm", "hygon_tpcm", "tpcm"}:
        return ADAPTER_OPENTCSM
    if trusted_root_type == TRUST_ROOT_TPM:
        return ADAPTER_KEYLIME
    if trusted_root_type == TRUST_ROOT_TPCM:
        return ADAPTER_OPENTCSM
    return normalized


def _normalize_trusted_root_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if "tpcm" in normalized:
        return TRUST_ROOT_TPCM
    if "tpm" in normalized:
        return TRUST_ROOT_TPM
    return TRUST_ROOT_UNKNOWN


def _agent_endpoint(node: ComputeNode, adapter_type: str) -> dict[str, Any]:
    if adapter_type == ADAPTER_KEYLIME:
        return {
            "host": node.keylime_agent_ip or node.management_ip,
            "port": node.keylime_agent_port,
        }
    if adapter_type == ADAPTER_OPENTCSM:
        return {
            "transport": "ssh",
            "host": node.management_ip,
        }
    return {}


def _agent_identity(
    node: ComputeNode,
    adapter_type: str,
    facts: dict[str, Any],
) -> dict[str, Any]:
    if adapter_type == ADAPTER_KEYLIME:
        return {
            "keylime_agent_uuid": node.keylime_agent_uuid,
        }
    if adapter_type == ADAPTER_OPENTCSM:
        return {
            "tpcm_id": str(facts.get("tpcm_id") or ""),
        }
    return {}


def _capabilities(
    *,
    trusted_root_type: str,
    adapter_type: str,
    trust_managed: bool,
) -> dict[str, bool]:
    if not trust_managed:
        return {}
    if trusted_root_type == TRUST_ROOT_TPM or adapter_type == ADAPTER_KEYLIME:
        return {
            CAPABILITY_TRUSTED_BOOT: True,
            CAPABILITY_IMA_RUNTIME: True,
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: False,
            CAPABILITY_EVM: False,
        }
    if trusted_root_type == TRUST_ROOT_TPCM or adapter_type == ADAPTER_OPENTCSM:
        return {
            CAPABILITY_TRUSTED_BOOT: True,
            CAPABILITY_IMA_RUNTIME: False,
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: True,
            CAPABILITY_EVM: False,
        }
    return {}


def _normalize_capabilities(
    capabilities: dict[str, Any],
    *,
    trusted_root_type: str,
    adapter_type: str,
    trust_managed: bool,
) -> dict[str, bool]:
    if not trust_managed:
        return {}
    defaults = _capabilities(
        trusted_root_type=trusted_root_type,
        adapter_type=adapter_type,
        trust_managed=trust_managed,
    )
    if not capabilities:
        return defaults
    if trusted_root_type == TRUST_ROOT_TPM or adapter_type == ADAPTER_KEYLIME:
        return {
            CAPABILITY_TRUSTED_BOOT: bool(
                capabilities.get(CAPABILITY_TRUSTED_BOOT, defaults.get(CAPABILITY_TRUSTED_BOOT))
            ),
            CAPABILITY_IMA_RUNTIME: bool(
                capabilities.get(CAPABILITY_IMA_RUNTIME, defaults.get(CAPABILITY_IMA_RUNTIME))
            ),
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: False,
            CAPABILITY_EVM: False,
        }
    if trusted_root_type == TRUST_ROOT_TPCM or adapter_type == ADAPTER_OPENTCSM:
        return {
            CAPABILITY_TRUSTED_BOOT: bool(
                capabilities.get(CAPABILITY_TRUSTED_BOOT, defaults.get(CAPABILITY_TRUSTED_BOOT))
            ),
            CAPABILITY_IMA_RUNTIME: False,
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: bool(
                capabilities.get(
                    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
                    defaults.get(CAPABILITY_TPCM_DYNAMIC_MEASUREMENT),
                )
            ),
            CAPABILITY_EVM: False,
        }
    return {}
