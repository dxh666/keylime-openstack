"""Worker synchronization workflow."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import TRUST_AGENT_KEYLIME
from keylime_openstack.models import AuditEvent, ComputeNode, OpenStackState, TrustDecision
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.decision import evaluate_trust
from keylime_openstack.services.openstack import OpenStackClient
from keylime_openstack.services.sync_collectors import (
    OpenStackStateCollector,
    TrustEvidenceCollector,
    keylime_status_to_evidence,
    latest_evidence_for_decision,
)
from keylime_openstack.services.trust_agents import (
    node_trust_agent_type,
    node_trust_managed,
    node_trusted_root,
    node_trusted_root_type,
)


class TrustSyncService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.openstack = OpenStackClient(settings)
        self.openstack_states = OpenStackStateCollector(session, self.openstack)
        self.evidence_collector = TrustEvidenceCollector(session, settings)

    def run_once(self) -> dict[str, object]:
        ensure_default_environment(self.session)
        self.session.flush()
        nodes = self._enabled_compute_nodes()
        self.refresh_openstack_states(nodes)
        results = [self.sync_node(node) for node in nodes]
        self.session.commit()
        return {"nodes": len(results), "results": results}

    def _enabled_compute_nodes(self) -> list[ComputeNode]:
        return list(
            self.session.scalars(
                select(ComputeNode)
                .where(ComputeNode.role == "compute")
                .where(ComputeNode.enabled.is_(True))
            ).all()
        )

    def refresh_openstack_states(self, nodes: list[ComputeNode]) -> None:
        self.openstack_states.refresh(nodes)

    def sync_node(self, node: ComputeNode) -> dict[str, object]:
        trust_agent_type = node_trust_agent_type(node, self.settings)
        trust_agent_result = self.evidence_collector.collect_for_node(node)
        evidence = latest_evidence_for_decision(self.session, node)
        openstack_state = self._latest_openstack_state(node)
        decision_data = evaluate_trust(
            evidence=list(evidence),
            openstack_state=openstack_state,
            settings=self.settings,
        )
        decision = TrustDecision(
            node_id=node.id,
            boot_trusted=decision_data["boot_trusted"],
            runtime_trusted=decision_data["runtime_trusted"],
            trusted=decision_data["trusted"],
            reason=decision_data["reason"],
            desired_traits=decision_data["desired_traits"],
            evidence_refs=decision_data["evidence_refs"],
            decision_details=decision_data["details"],
        )
        self.session.add(decision)

        trait_result = self.openstack.set_provider_traits(
            node.hypervisor_name or node.hostname,
            decision.desired_traits,
        )
        self.session.add(
            AuditEvent(
                event_type="trust_decision",
                target=node.hostname,
                severity="info" if decision.trusted else "warning",
                message=decision.reason,
                event_details={
                    "desired_traits": decision.desired_traits,
                    "trait_result": trait_result,
                    "trust_managed": node_trust_managed(node, self.settings),
                    "trusted_root_type": node_trusted_root_type(node, self.settings),
                    "trusted_root": node_trusted_root(node, self.settings),
                    "trust_agent_type": trust_agent_type,
                },
            )
        )
        return {
            "host": node.hostname,
            "trusted": decision.trusted,
            "reason": decision.reason,
            "desired_traits": decision.desired_traits,
            "trust_agent": trust_agent_result,
            "trust_managed": node_trust_managed(node, self.settings),
            "trusted_root_type": node_trusted_root_type(node, self.settings),
            "trusted_root": node_trusted_root(node, self.settings),
            "keylime": trust_agent_result if trust_agent_type == TRUST_AGENT_KEYLIME else {},
        }

    def _latest_openstack_state(self, node: ComputeNode) -> OpenStackState | None:
        return self.session.scalars(
            select(OpenStackState)
            .where(OpenStackState.node_id == node.id)
            .order_by(OpenStackState.updated_at.desc())
            .limit(1)
        ).first()
