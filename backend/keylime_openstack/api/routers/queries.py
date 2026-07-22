"""Shared query helpers for API route modules."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.models import OpenStackState, TrustDecision


def _latest_openstack_states(session: Session, node_ids: list[int]) -> dict[int, OpenStackState]:
    if not node_ids:
        return {}
    rows = session.scalars(
        select(OpenStackState)
        .where(OpenStackState.node_id.in_(node_ids))
        .order_by(OpenStackState.updated_at.desc())
    ).all()
    latest: dict[int, OpenStackState] = {}
    for row in rows:
        latest.setdefault(row.node_id, row)
    return latest


def _latest_decisions(session: Session, limit: int, node_ids: list[int] | None = None) -> list[TrustDecision]:
    if node_ids == []:
        return []
    statement = select(TrustDecision).order_by(TrustDecision.decided_at.desc()).limit(limit)
    if node_ids is not None:
        statement = statement.where(TrustDecision.node_id.in_(node_ids))
    rows = session.scalars(statement).all()
    latest: dict[int, TrustDecision] = {}
    for row in rows:
        latest.setdefault(row.node_id, row)
    return list(latest.values())
