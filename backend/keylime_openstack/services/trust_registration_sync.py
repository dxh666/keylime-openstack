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
    ADAPTER_OPENTCSM,
    CAPABILITY_EVM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    PROVIDER_OPENTCSM,
    REGISTRATION_CONFLICT,
    REGISTRATION_REGISTERED,
    REGISTRATION_UNTRUSTED,
    REGISTRATION_VERIFIED,
    REGISTRATION_VERIFIER_ENROLLED,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_ROOT_TPCM,
    TRUST_ROOT_TPM,
)
from keylime_openstack.models import ComputeNode, EvidenceRecord
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.trust_agents import normalize_trust_agent_type, parse_host_map
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
    tpcm_result = _sync_tpcm_inventory_profiles(session, settings, nodes)
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
        "tpcm_registrations": tpcm_result["matched"],
        "keylime_agents_discovered": len(discovered_agents),
        "keylime_agents_matched": remote_result["matched"],
        "keylime_agents_unmatched": remote_result["unmatched_agents"],
        "registration_conflicts": [
            *static_result["conflicts"],
            *tpcm_result["conflicts"],
            *remote_result["conflicts"],
        ],
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
    conflicts: list[dict[str, Any]] = []
    for node in nodes:
        host_key = _mapped_node_key(node, [*hosts, *ip_map.keys(), *uuid_map.keys()])
        if not host_key:
            continue
        agent_uuid = uuid_map.get(host_key) or node.keylime_agent_uuid
        agent_ip = ip_map.get(host_key) or node.keylime_agent_ip or node.management_ip
        if not agent_uuid:
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
                "reason": "conflicting-static-keylime-registration",
                "static_keylime_agent_uuid": agent_uuid,
            }
            conflicts.append(
                {
                    "node": node.hostname,
                    "existing_adapter_type": profile.adapter_type,
                    "discovered_adapter_type": ADAPTER_KEYLIME,
                    "agent_uuid": agent_uuid,
                    "source": "static-env-map",
                }
            )
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
    return {"matched": matched, "conflicts": conflicts}


def _sync_tpcm_inventory_profiles(
    session: Session,
    settings: Settings,
    nodes: list[ComputeNode],
) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for node in nodes:
        candidate = _tpcm_registration_candidate(session, node)
        if not candidate:
            continue

        profile = ensure_trusted_node_profile(session, node, settings)
        if (
            profile.trust_managed
            and profile.adapter_type
            and profile.adapter_type != ADAPTER_OPENTCSM
        ):
            profile.registration_status = REGISTRATION_CONFLICT
            profile.last_evidence_summary = {
                **dict(profile.last_evidence_summary or {}),
                "trusted": False,
                "reason": "conflicting-tpcm-registration",
                "tpcm_id": candidate.get("tpcm_id") or "",
            }
            conflicts.append(
                {
                    "node": node.hostname,
                    "existing_adapter_type": profile.adapter_type,
                    "discovered_adapter_type": ADAPTER_OPENTCSM,
                    "tpcm_id": candidate.get("tpcm_id") or "",
                    "source": candidate.get("source") or "",
                }
            )
            continue

        profile = upsert_trusted_node_profile(
            session,
            node,
            settings,
            _tpcm_registration_payload(node, candidate),
        )
        evidence_summary = candidate.get("evidence_summary")
        if isinstance(evidence_summary, dict) and evidence_summary:
            profile.last_evidence_summary = evidence_summary
            if candidate.get("verified_at"):
                profile.last_verified_at = candidate["verified_at"]
        matched.append(
            {
                "node": node.hostname,
                "tpcm_id": candidate.get("tpcm_id") or "",
                "registration_status": profile.registration_status,
                "source": candidate.get("source") or "",
            }
        )
    return {"matched": matched, "conflicts": conflicts}


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


def _tpcm_registration_payload(
    node: ComputeNode,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    tpcm_id = str(candidate.get("tpcm_id") or "")
    source = str(candidate.get("source") or "tpcm-inventory")
    return {
        "hostname": node.hostname,
        "openstack_compute_name": node.hypervisor_name or node.hostname,
        "management_ip": node.management_ip,
        "is_openstack_compute": node.role == "compute",
        "trust_managed": True,
        "trusted_root_type": TRUST_ROOT_TPCM,
        "adapter_type": ADAPTER_OPENTCSM,
        "agent_endpoint": {
            "transport": "ssh",
            "host": node.management_ip,
        },
        "agent_identity": {
            "tpcm_id": tpcm_id,
            "registration_source": source,
        },
        "capabilities": {
            CAPABILITY_TRUSTED_BOOT: True,
            CAPABILITY_IMA_RUNTIME: False,
            CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: True,
            CAPABILITY_EVM: False,
        },
        "registration_status": candidate.get("registration_status") or REGISTRATION_REGISTERED,
    }


def _tpcm_registration_candidate(session: Session, node: ComputeNode) -> dict[str, Any] | None:
    facts = node.facts or {}
    latest_evidence = _latest_opentcsm_evidence(session, node)
    evidence_payload = latest_evidence.payload if latest_evidence else {}
    evidence_raw = (
        evidence_payload.get("raw")
        if isinstance(evidence_payload, dict) and isinstance(evidence_payload.get("raw"), dict)
        else {}
    )
    fact_agent_type = normalize_trust_agent_type(str(facts.get("trust_agent_type") or ""))
    fact_agent_name = str(facts.get("trust_agent_name") or facts.get("agent_name") or "").lower()
    explicit_tpcm_agent = (
        fact_agent_type == TRUST_AGENT_OPENTCSM_TPCM
        or "opentcsm" in fact_agent_name
        or _truthy(facts.get("opentcsm_agent_enabled"))
        or _truthy(facts.get("tpcm_agent_enabled"))
    )
    if not explicit_tpcm_agent and latest_evidence is None:
        return None

    tpcm_id = str(facts.get("tpcm_id") or evidence_raw.get("tpcm_id") or "")
    source = "opentcsm-evidence" if latest_evidence is not None else "node-inventory-facts"
    candidate: dict[str, Any] = {
        "tpcm_id": tpcm_id,
        "source": source,
        "registration_status": REGISTRATION_REGISTERED,
    }
    if latest_evidence is not None:
        trusted = _evidence_payload_trusted(evidence_payload)
        if trusted is True:
            candidate["registration_status"] = REGISTRATION_VERIFIED
        elif trusted is False:
            candidate["registration_status"] = REGISTRATION_UNTRUSTED
        else:
            candidate["registration_status"] = REGISTRATION_REGISTERED
        candidate["verified_at"] = latest_evidence.collected_at
        candidate["evidence_summary"] = _opentcsm_evidence_summary(latest_evidence)
    return candidate


def _latest_opentcsm_evidence(session: Session, node: ComputeNode) -> EvidenceRecord | None:
    return session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.node_id == node.id)
        .where(EvidenceRecord.provider == PROVIDER_OPENTCSM)
        .order_by(EvidenceRecord.collected_at.desc(), EvidenceRecord.id.desc())
        .limit(1)
    ).first()


def _opentcsm_evidence_summary(record: EvidenceRecord) -> dict[str, Any]:
    payload = record.payload or {}
    raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else {}
    boot = raw.get("boot_records") if isinstance(raw.get("boot_records"), list) else []
    dynamic = raw.get("dmeasure_policy") if isinstance(raw.get("dmeasure_policy"), list) else []
    return {
        "trusted": _evidence_payload_trusted(payload),
        "evidence": {
            "boot": raw.get("boot_status") or (record.status if record.evidence_type == "boot" else "unknown"),
            "runtime": raw.get("dynamic_measurement_status")
            or (record.status if record.evidence_type == "runtime" else "unknown"),
        },
        "boot_measurement_summary": {
            "type": "tpcm_boot_measurement",
            "enabled": raw.get("boot_measure_on"),
            "status": raw.get("boot_status") or record.status or "unknown",
            "record_count": len(boot),
            "reference_count": raw.get("boot_measure_ref_number"),
            "records_preview": boot[:5],
            "baseline_ready": bool(raw.get("boot_measure_ref_number")),
            "records_sha256": raw.get("boot_measure_records_sha256") or "",
            "trust_report_sha256": raw.get("trust_report_sha256") or "",
        },
        "dynamic_measurement_summary": {
            "type": "tpcm_dynamic_measurement",
            "enabled": raw.get("dynamic_measure_on"),
            "status": raw.get("dynamic_measurement_status") or "unknown",
            "object_count": len(dynamic),
            "dmeasure_times": raw.get("dmeasure_times"),
            "policy_sha256": raw.get("dmeasure_policy_sha256") or "",
        },
        "reason": record.summary,
        "source": PROVIDER_OPENTCSM,
    }


def _evidence_payload_trusted(payload: dict[str, Any]) -> bool | None:
    trusted = payload.get("trusted") if isinstance(payload, dict) else None
    return trusted if isinstance(trusted, bool) else None


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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "enabled", "on"}
