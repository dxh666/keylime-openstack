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
from keylime_openstack.services.trust_agents import (
    node_trust_agent_name,
    node_trust_agent_type,
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
        select(ComputeNode).options(joinedload(ComputeNode.hardware_profile)).order_by(ComputeNode.hostname)
    ).all()
    states = _latest_openstack_states(session, [item.id for item in rows])
    return [
        ComputeNodeOut.model_validate(
            {
                **item.__dict__,
                "trust_agent_type": node_trust_agent_type(item, settings),
                "trust_agent_name": node_trust_agent_name(item, settings),
                "trusted_root": node_trusted_root(item, settings),
                "hardware_profile": item.hardware_profile,
                "openstack_state": states.get(item.id),
            }
        )
        for item in rows
    ]


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
