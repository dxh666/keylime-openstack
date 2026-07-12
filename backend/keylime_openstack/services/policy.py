"""Policy lookup and binding helpers."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.constants import BINDING_HARDWARE_PROFILE, BINDING_NODE
from keylime_openstack.models import ComputeNode, PolicyBinding, TrustPolicy


def effective_policies_for_node(session: Session, node: ComputeNode) -> list[TrustPolicy]:
    """Return node-specific policies first, then hardware-profile policies."""

    bindings = []
    node_bindings = session.scalars(
        select(PolicyBinding)
        .where(PolicyBinding.active.is_(True))
        .where(PolicyBinding.target_type == BINDING_NODE)
        .where(PolicyBinding.target_id == node.id)
        .order_by(PolicyBinding.priority)
    ).all()
    bindings.extend(node_bindings)

    if node.hardware_profile_id:
        profile_bindings = session.scalars(
            select(PolicyBinding)
            .where(PolicyBinding.active.is_(True))
            .where(PolicyBinding.target_type == BINDING_HARDWARE_PROFILE)
            .where(PolicyBinding.target_id == node.hardware_profile_id)
            .order_by(PolicyBinding.priority)
        ).all()
        bindings.extend(profile_bindings)

    seen: set[int] = set()
    policies = []
    for binding in bindings:
        if binding.policy_id in seen:
            continue
        seen.add(binding.policy_id)
        policies.append(binding.policy)
    return policies
