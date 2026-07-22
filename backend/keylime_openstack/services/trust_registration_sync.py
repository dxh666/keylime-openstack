"""Trusted-node registration synchronization.

This module turns adapter-level inventory into the product registration model:
OpenStack compute node, managed/unmanaged trusted agent, trusted root type, and
capabilities. Keylime/OpenTCSM stay behind the adapter boundary.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    ADAPTER_KEYLIME,
    CAPABILITY_EVM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    REGISTRATION_CONFLICT,
    REGISTRATION_REGISTERED,
    REGISTRATION_VERIFIER_ENROLLED,
    TRUST_ROOT_TPCM,
    TRUST_ROOT_TPM,
)
from keylime_openstack.models import ComputeNode
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.trust_agents import parse_host_map
from keylime_openstack.services.trust_registration import (
    ensure_trusted_node_profile,
    profile_payload,
    upsert_trusted_node_profile,
)

__all__ = ["sync_trusted_node_registrations"]


def sync_trusted_node_registrations(
    session: Session,
    settings: Settings,
    *,
    nodes: list[ComputeNode] | None = None,
    discover_keylime: bool = True,
    use_tenant_tool: bool | None = None,
) -> dict[str, Any]:
    """Synchronize product registration profiles from available inventories."""

    if nodes is None:
        nodes = list(
            session.scalars(
                select(ComputeNode)
                .options(joinedload(ComputeNode.trust_profile))
                .where(ComputeNode.role == "compute")
                .where(ComputeNode.enabled.is_(True))
                .order_by(ComputeNode.hostname)
            ).all()
        )

    for node in nodes:
        ensure_trusted_node_profile(session, node, settings)
    session.flush()

    static_result = _sync_static_keylime_maps(session, settings, nodes)
    discovered_agents: list[dict[str, Any]] = []
    discovery_error = ""
    remote_result = {"matched": [], "unmatched_agents": [], "conflicts": []}
    if discover_keylime and settings.keylime_auto_registration_enabled:
        try:
            discovered_agents = KeylimeClient(settings).list_registered_agents(
                use_tenant_tool=(
                    settings.keylime_auto_registration_use_tenant_tool
                    if use_tenant_tool is None
                    else use_tenant_tool
                )
            )
        except Exception as exc:  # pragma: no cover - deployment-specific API boundary
            discovery_error = str(exc)
        else:
            remote_result = _sync_discovered_keylime_agents(
                session,
                settings,
                nodes,
                discovered_agents,
            )

    session.flush()
    profiles = [
        profile_payload(ensure_trusted_node_profile(session, node, settings), node)
        for node in nodes
    ]
    return {
        "ok": not discovery_error,
        "nodes_seen": len(nodes),
        "profiles_total": len(profiles),
        "static_keylime_registrations": static_result["matched"],
        "keylime_agents_discovered": len(discovered_agents),
        "keylime_agents_matched": remote_result["matched"],
        "keylime_agents_unmatched": remote_result["unmatched_agents"],
        "registration_conflicts": remote_result["conflicts"],
        "discovery_error": discovery_error,
        "profiles": profiles,
    }


def _sync_static_keylime_maps(
    session: Session,
    settings: Settings,
    nodes: list[ComputeNode],
) -> dict[str, Any]:
    hosts = {item.strip() for item in settings.keylime_agent_hosts.split(",") if item.strip()}
    ip_map = parse_host_map(settings.keylime_agent_ip_map)
    uuid_map = parse_host_map(settings.keylime_agent_uuid_map)
    matched: list[dict[str, Any]] = []
    for node in nodes:
        host_key = _mapped_node_key(node, [*hosts, *ip_map.keys(), *uuid_map.keys()])
        if not host_key:
            continue
        agent_uuid = uuid_map.get(host_key) or node.keylime_agent_uuid
        agent_ip = ip_map.get(host_key) or node.keylime_agent_ip or node.management_ip
        if not agent_uuid:
            continue
        node.keylime_agent_uuid = agent_uuid
        node.keylime_agent_ip = agent_ip
        node.keylime_agent_port = node.keylime_agent_port or settings.keylime_agent_port
        profile = upsert_trusted_node_profile(
            session,
            node,
            settings,
            _keylime_registration_payload(
                node,
                agent_uuid=agent_uuid,
                agent_ip=agent_ip,
                source="static-env-map",
                registration_status=REGISTRATION_REGISTERED,
            ),
        )
        matched.append(
            {
                "node": node.hostname,
                "agent_uuid": agent_uuid,
                "agent_ip": agent_ip,
                "registration_status": profile.registration_status,
                "source": "static-env-map",
            }
        )
    return {"matched": matched}


def _sync_discovered_keylime_agents(
    session: Session,
    settings: Settings,
    nodes: list[ComputeNode],
    agents: list[dict[str, Any]],
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for agent in agents:
        node = _match_agent_to_node(agent, nodes)
        agent_uuid = str(agent.get("agent_uuid") or "")
        agent_ip = str(agent.get("ip") or "")
        if not node:
            unmatched.append(
                {
                    "agent_uuid": agent_uuid,
                    "agent_ip": agent_ip,
                    "hostname": agent.get("hostname") or "",
                    "source": agent.get("source") or "",
                }
            )
            continue

        profile = ensure_trusted_node_profile(session, node, settings)
        if (
            profile.trust_managed
            and profile.adapter_type
            and profile.adapter_type != ADAPTER_KEYLIME
        ):
            profile.registration_status = REGISTRATION_CONFLICT
            profile.last_evidence_summary = {
                **dict(profile.last_evidence_summary or {}),
                "trusted": False,
                "reason": "conflicting-trust-agent-adapter",
                "discovered_keylime_agent_uuid": agent_uuid,
            }
            conflicts.append(
                {
                    "node": node.hostname,
                    "existing_adapter_type": profile.adapter_type,
                    "discovered_adapter_type": ADAPTER_KEYLIME,
                    "agent_uuid": agent_uuid,
                }
            )
            continue
        if profile.trusted_root_type == TRUST_ROOT_TPCM and profile.adapter_type != ADAPTER_KEYLIME:
            profile.registration_status = REGISTRATION_CONFLICT
            profile.last_evidence_summary = {
                **dict(profile.last_evidence_summary or {}),
                "trusted": False,
                "reason": "conflicting-trusted-root-type",
                "discovered_keylime_agent_uuid": agent_uuid,
            }
            conflicts.append(
                {
                    "node": node.hostname,
                    "trusted_root_type": profile.trusted_root_type,
                    "discovered_trusted_root_type": TRUST_ROOT_TPM,
                    "agent_uuid": agent_uuid,
                }
            )
            continue

        node.keylime_agent_uuid = agent_uuid
        if agent_ip:
            node.keylime_agent_ip = agent_ip
        node.keylime_agent_port = int(
            agent.get("port") or node.keylime_agent_port or settings.keylime_agent_port
        )
        profile = upsert_trusted_node_profile(
            session,
            node,
            settings,
            _keylime_registration_payload(
                node,
                agent_uuid=agent_uuid,
                agent_ip=agent_ip or node.keylime_agent_ip or node.management_ip,
                source=str(agent.get("source") or "keylime-discovery"),
                registration_status=REGISTRATION_VERIFIER_ENROLLED
                if str(agent.get("source") or "").startswith("verifier")
                else REGISTRATION_REGISTERED,
            ),
        )
        matched.append(
            {
                "node": node.hostname,
                "agent_uuid": agent_uuid,
                "agent_ip": agent_ip or node.keylime_agent_ip,
                "registration_status": profile.registration_status,
                "source": agent.get("source") or "",
            }
        )
    return {"matched": matched, "unmatched_agents": unmatched, "conflicts": conflicts}


def _keylime_registration_payload(
    node: ComputeNode,
    *,
    agent_uuid: str,
    agent_ip: str,
    source: str,
    registration_status: str,
) -> dict[str, Any]:
    return {
        "hostname": node.hostname,
        "openstack_compute_name": node.hypervisor_name or node.hostname,
        "management_ip": node.management_ip,
        "is_openstack_compute": node.role == "compute",
        "trust_managed": True,
        "trusted_root_type": TRUST_ROOT_TPM,
        "adapter_type": ADAPTER_KEYLIME,
        "agent_endpoint": {
            "host": agent_ip or node.management_ip,
            "port": node.keylime_agent_port or 9002,
        },
        "agent_identity": {
            "keylime_agent_uuid": agent_uuid,
            "registration_source": source,
        },
        "capabilities": {
            CAPABILITY_TRUSTED_BOOT: True,
            CAPABILITY_IMA_RUNTIME: True,
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: False,
            CAPABILITY_EVM: False,
        },
        "registration_status": registration_status,
    }


def _match_agent_to_node(agent: dict[str, Any], nodes: list[ComputeNode]) -> ComputeNode | None:
    agent_uuid = str(agent.get("agent_uuid") or "")
    agent_ip = str(agent.get("ip") or "")
    agent_hostname = str(agent.get("hostname") or "")
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    names = {
        _normalize_name(agent_hostname),
        _normalize_name(str(metadata.get("hostname") or "")),
        _normalize_name(str(metadata.get("host") or "")),
        _normalize_name(str(metadata.get("openstack_compute_name") or "")),
    }
    names = {item for item in names if item}
    for node in nodes:
        if agent_uuid and node.keylime_agent_uuid == agent_uuid:
            return node
    for node in nodes:
        node_names = {
            _normalize_name(node.hostname),
            _normalize_name(node.hypervisor_name),
        }
        if names and names.intersection(node_names):
            return node
    if agent_ip:
        for node in nodes:
            if agent_ip in {node.management_ip, node.keylime_agent_ip}:
                return node
    return None


def _mapped_node_key(node: ComputeNode, keys: list[str]) -> str:
    node_names = {_normalize_name(node.hostname), _normalize_name(node.hypervisor_name)}
    for key in keys:
        if _normalize_name(key) in node_names:
            return key
    return ""


def _normalize_name(value: str) -> str:
    text = str(value or "").strip().lower()
    return text.split(".", 1)[0] if text else ""
