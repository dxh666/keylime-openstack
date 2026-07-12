"""Worker synchronization workflow."""

from __future__ import annotations

from typing import Any

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
        self.refresh_openstack_states(nodes)
        results = []
        for node in nodes:
            results.append(self.sync_node(node))
        self.session.commit()
        return {"nodes": len(results), "results": results}

    def refresh_openstack_states(self, nodes: list[ComputeNode]) -> None:
        """Collect nova-compute service state before evaluating trust."""

        try:
            services = self.openstack.list_compute_services()
        except Exception as exc:  # pragma: no cover - deployment-specific API boundary
            self.session.add(
                AuditEvent(
                    event_type="openstack_state_refresh",
                    target="nova-compute",
                    severity="error",
                    message=str(exc),
                    event_details={"adapter": "openstacksdk"},
                )
            )
            return

        services_by_host = {
            str(item.get("host") or item.get("Host") or ""): item
            for item in services
            if item.get("host") or item.get("Host")
        }
        for node in nodes:
            service = services_by_host.get(node.hypervisor_name) or services_by_host.get(node.hostname)
            if not service:
                continue
            self.session.add(
                OpenStackState(
                    node_id=node.id,
                    service_binary=str(service.get("binary") or service.get("Binary") or "nova-compute"),
                    service_status=str(service.get("status") or service.get("Status") or "unknown").lower(),
                    service_state=str(service.get("state") or service.get("State") or "unknown").lower(),
                    raw=_json_safe(service),
                )
            )

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


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
