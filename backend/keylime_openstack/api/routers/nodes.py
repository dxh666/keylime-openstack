"""Compute and hardware inventory API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from keylime_openstack.api.deps import db_session, settings_dep
from keylime_openstack.api.routers.queries import _latest_openstack_states
from keylime_openstack.config import Settings
from keylime_openstack.models import ComputeNode, HardwareProfile
from keylime_openstack.schemas import ComputeNodeOut
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.trust_registration import (
    ensure_trusted_node_profile,
    profile_payload,
)
from keylime_openstack.services.trust_agents import (
    node_trust_agent_name,
    node_trust_managed,
    node_trust_agent_type,
    node_trusted_root_type,
    node_trusted_root,
)

router = APIRouter()


@router.get("/nodes", response_model=list[ComputeNodeOut])
def nodes(
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> list[ComputeNodeOut]:
    ensure_default_environment(session)
    session.commit()
    rows = session.scalars(
        select(ComputeNode)
        .options(joinedload(ComputeNode.hardware_profile), joinedload(ComputeNode.trust_profile))
        .order_by(ComputeNode.hostname)
    ).all()
    profiles = {
        item.id: ensure_trusted_node_profile(session, item, settings)
        for item in rows
    }
    session.commit()
    states = _latest_openstack_states(session, [item.id for item in rows])
    result = []
    for item in rows:
        profile = profiles[item.id]
        profile_data = profile_payload(profile, item)
        result.append(
            ComputeNodeOut.model_validate(
                {
                    **item.__dict__,
                    "openstack_compute_name": profile_data["openstack_compute_name"],
                    "trust_agent_type": node_trust_agent_type(item, settings),
                    "trust_agent_name": node_trust_agent_name(item, settings),
                    "trust_managed": node_trust_managed(item, settings),
                    "trusted_root_type": node_trusted_root_type(item, settings),
                    "trusted_root": node_trusted_root(item, settings),
                    "adapter_type": profile_data["adapter_type"],
                    "agent_endpoint": profile_data["agent_endpoint"],
                    "agent_identity": profile_data["agent_identity"],
                    "capabilities": profile_data["capabilities"],
                    "registration_status": profile_data["registration_status"],
                    "last_verified_at": profile_data["last_verified_at"],
                    "last_evidence_summary": profile_data["last_evidence_summary"],
                    "trusted_node_profile": profile_data,
                    "hardware_profile": item.hardware_profile,
                    "openstack_state": states.get(item.id),
                }
            )
        )
    return result


@router.get("/hardware-profiles")
def hardware_profiles(session: Session = Depends(db_session)) -> list[dict[str, object]]:
    ensure_default_environment(session)
    session.commit()
    rows = session.scalars(select(HardwareProfile).order_by(HardwareProfile.name)).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "vendor": item.vendor,
            "model": item.model,
            "kernel_family": item.kernel_family,
        }
        for item in rows
    ]
