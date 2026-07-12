"""Worker synchronization workflow."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.models import AuditEvent, ComputeNode, EvidenceRecord, OpenStackState, TrustDecision
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.decision import evaluate_trust
from keylime_openstack.services.openstack import OpenStackClient


class TrustSyncService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.openstack = OpenStackClient(settings)

    def run_once(self) -> dict[str, object]:
        ensure_default_environment(self.session)
        self.session.flush()
        nodes = self.session.scalars(
            select(ComputeNode).where(ComputeNode.role == "compute").where(ComputeNode.enabled.is_(True))
        ).all()
        results = []
        for node in nodes:
            results.append(self.sync_node(node))
        self.session.commit()
        return {"nodes": len(results), "results": results}

    def sync_node(self, node: ComputeNode) -> dict[str, object]:
        evidence = self.session.scalars(
            select(EvidenceRecord)
            .where(EvidenceRecord.node_id == node.id)
            .order_by(EvidenceRecord.collected_at.desc())
            .limit(20)
        ).all()
        openstack_state = self.session.scalars(
            select(OpenStackState)
            .where(OpenStackState.node_id == node.id)
            .order_by(OpenStackState.updated_at.desc())
            .limit(1)
        ).first()
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
                },
            )
        )
        return {
            "host": node.hostname,
            "trusted": decision.trusted,
            "reason": decision.reason,
            "desired_traits": decision.desired_traits,
        }
