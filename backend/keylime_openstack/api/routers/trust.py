"""Product-level trusted-agent API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from keylime_openstack.api.deps import db_session, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.models import ComputeNode
from keylime_openstack.schemas import TrustAgentRegistrationIn, TrustedNodeProfileOut
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.audit import record_audit_event
from keylime_openstack.services.keylime_gate import keylime_only_check
from keylime_openstack.services.trust_registration import (
    profile_payload,
    upsert_trusted_node_profile,
)
from keylime_openstack.services.trust_registration_sync import sync_trusted_node_registrations
from keylime_openstack.services.trust_verification import verify_trusted_node

router = APIRouter()


@router.get("/trust/check")
def trust_check(
    hosts: str = "",
    failures_only: bool = False,
) -> dict[str, object]:
    result = keylime_only_check(
        hosts=hosts,
        failures_only=failures_only,
        include_non_keylime=True,
    )
    result["mode"] = "trusted-agent-check"
    return result


@router.get("/trust/registrations")
def trust_registrations(
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    ensure_default_environment(session)
    result = sync_trusted_node_registrations(
        session,
        settings,
        discover_keylime=False,
    )
    session.commit()
    return {
        "ok": True,
        "profiles_total": result["profiles_total"],
        "profiles": result["profiles"],
        "static_keylime_registrations": result["static_keylime_registrations"],
        "tpcm_registrations": result["tpcm_registrations"],
    }


@router.post("/trust/registrations/sync")
def sync_trust_registrations(
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    ensure_default_environment(session)
    try:
        result = sync_trusted_node_registrations(
            session,
            settings,
            discover_keylime=True,
            use_tenant_tool=settings.keylime_auto_registration_use_tenant_tool,
        )
    except Exception:
        session.rollback()
        raise
    record_audit_event(
        session,
        event_type="trusted_node_registration_sync",
        target="trusted-agent",
        severity="info" if result.get("ok") else "warning",
        message="trusted node registrations synchronized",
        event_details={
            "log_type": "node_management",
            "subject_name": "可信代理",
            "object_name": "计算节点",
            "operation": "同步纳管",
            "result": "成功" if result.get("ok") else "异常",
            "nodes_seen": result.get("nodes_seen"),
            "static_keylime_registrations": result.get("static_keylime_registrations"),
            "tpcm_registrations": result.get("tpcm_registrations"),
            "keylime_agents_discovered": result.get("keylime_agents_discovered"),
            "keylime_agents_matched": result.get("keylime_agents_matched"),
            "registration_conflicts": result.get("registration_conflicts"),
            "manual_profiles_preserved": result.get("manual_profiles_preserved"),
            "discovery_error": result.get("discovery_error"),
        },
    )
    session.commit()
    return result


@router.post("/nodes/{hostname}/trust-agent-verify")
def verify_trust_agent(
    hostname: str,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    ensure_default_environment(session)
    node = session.scalars(
        select(ComputeNode)
        .options(joinedload(ComputeNode.trust_profile))
        .where((ComputeNode.hostname == hostname) | (ComputeNode.hypervisor_name == hostname))
    ).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"unknown compute node {hostname}")

    try:
        result = verify_trusted_node(session, settings, node)
    except Exception as exc:
        session.rollback()
        record_audit_event(
            session,
            event_type="trusted_node_verify",
            target=node.hostname,
            severity="error",
            message="trusted node verification failed",
            event_details={
                "log_type": "trust_verification",
                "subject_name": "可信代理",
                "object_name": node.hostname,
                "operation": "可信验证",
                "result": "失败",
                "error": str(exc),
            },
        )
        session.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    record_audit_event(
        session,
        event_type="trusted_node_verify",
        target=node.hostname,
        severity="info" if result.get("trusted") is True else "warning",
        message="trusted node verification completed",
        event_details={
            "log_type": "trust_verification",
            "subject_name": "可信代理",
            "object_name": node.hostname,
            "operation": "可信验证",
            "result": "成功" if result.get("trusted") is True else "异常",
            "trusted": result.get("trusted"),
            "status": result.get("status"),
            "trusted_node_profile": result.get("trusted_node_profile"),
            "evidence_summary": result.get("evidence_summary"),
        },
    )
    session.commit()
    return result


@router.put("/nodes/{hostname}/trusted-node-profile", response_model=TrustedNodeProfileOut)
def register_trust_agent(
    hostname: str,
    payload: TrustAgentRegistrationIn,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> TrustedNodeProfileOut:
    ensure_default_environment(session)
    node = session.scalars(
        select(ComputeNode)
        .options(joinedload(ComputeNode.trust_profile))
        .where((ComputeNode.hostname == hostname) | (ComputeNode.hypervisor_name == hostname))
    ).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"unknown compute node {hostname}")

    registration = payload.model_dump()
    registration["registration_source"] = "manual"
    profile = upsert_trusted_node_profile(session, node, settings, registration)
    record_audit_event(
        session,
        event_type="trusted_node_register",
        target=node.hostname,
        severity="info",
        message="trusted node registration updated",
        event_details={
            "log_type": "node_management",
            "subject_name": "可信代理",
            "object_name": node.hostname,
            "operation": "更新纳管参数",
            "result": "成功",
            "trusted_node_profile": profile_payload(profile, node),
        },
    )
    session.commit()
    return TrustedNodeProfileOut.model_validate(profile_payload(profile, node))
