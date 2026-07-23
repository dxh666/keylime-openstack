"""Worker synchronization workflow."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    CAPABILITY_EVM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    REGISTRATION_UNMANAGED,
    REGISTRATION_UNTRUSTED,
    REGISTRATION_VERIFIED,
    TRUST_AGENT_KEYLIME,
)
from keylime_openstack.models import ComputeNode, OpenStackState, TrustDecision, TrustedNodeProfile
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.audit import record_audit_event
from keylime_openstack.services.decision import evaluate_trust, unmanaged_trust_decision
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
from keylime_openstack.services.trust_capabilities import build_trust_capability_summary
from keylime_openstack.services.trust_registration import ensure_trusted_node_profile
from keylime_openstack.services.trust_registration_sync import sync_trusted_node_registrations


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
        registration_result = sync_trusted_node_registrations(
            self.session,
            self.settings,
            nodes=nodes,
            discover_keylime=self.settings.keylime_auto_registration_enabled,
        )
        self.refresh_openstack_states(nodes)
        results = [self.sync_node(node) for node in nodes]
        self.session.commit()
        return {"nodes": len(results), "registration": registration_result, "results": results}

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
        profile = ensure_trusted_node_profile(self.session, node, self.settings)
        trust_agent_type = node_trust_agent_type(node, self.settings)
        trust_managed = node_trust_managed(node, self.settings)
        trust_agent_result = self.evidence_collector.collect_for_node(node)
        evidence = latest_evidence_for_decision(self.session, node)
        capability_summary = build_trust_capability_summary(
            self.session,
            node,
            profile,
            evidence,
        )
        openstack_state = self._latest_openstack_state(node)
        if trust_managed:
            decision_data = evaluate_trust(
                evidence=list(evidence),
                openstack_state=openstack_state,
                settings=self.settings,
                capabilities=_decision_capabilities(self.settings, profile),
                runtime_capability=_runtime_capability(profile),
                capability_summary=capability_summary,
            )
        else:
            decision_data = unmanaged_trust_decision(openstack_state)
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
        _update_profile_from_decision(
            self.session,
            node,
            profile,
            decision_data,
            evidence,
            trust_agent_result,
            capability_summary,
        )

        trait_result = self.openstack.set_provider_traits(
            node.hypervisor_name or node.hostname,
            decision.desired_traits,
        )
        record_audit_event(
            self.session,
            event_type="trust_decision",
            target=node.hostname,
            severity="info" if decision.trusted else "warning",
            message=decision.reason,
            event_details={
                "desired_traits": decision.desired_traits,
                "trait_result": trait_result,
                "trust_managed": trust_managed,
                "trusted_root_type": node_trusted_root_type(node, self.settings),
                "trusted_root": node_trusted_root(node, self.settings),
                "trust_agent_type": trust_agent_type,
                "trust_management_status": trust_agent_result.get("status"),
            },
        )
        return {
            "host": node.hostname,
            "trusted": decision.trusted,
            "reason": decision.reason,
            "desired_traits": decision.desired_traits,
            "trust_agent": trust_agent_result,
            "trust_managed": trust_managed,
            "trusted_root_type": node_trusted_root_type(node, self.settings),
            "trusted_root": node_trusted_root(node, self.settings),
            "keylime": trust_agent_result if trust_agent_type == TRUST_AGENT_KEYLIME else {},
        }

    def _latest_openstack_state(self, node: ComputeNode) -> OpenStackState | None:
        return self.session.scalars(
            select(OpenStackState)
            .where(OpenStackState.node_id == node.id)
            .order_by(OpenStackState.updated_at.desc(), OpenStackState.id.desc())
            .limit(1)
        ).first()


def _update_profile_from_decision(
    session: Session,
    node: ComputeNode,
    profile: TrustedNodeProfile,
    decision_data: dict[str, object],
    evidence: list[object],
    trust_agent_result: dict[str, object],
    capability_summary: dict[str, dict[str, object]] | None = None,
) -> None:
    trust_managed = bool(profile.trust_managed)
    trusted = bool(decision_data.get("trusted"))
    evidence_status = {
        str(record.evidence_type): str(record.status)
        for record in evidence
        if hasattr(record, "evidence_type") and hasattr(record, "status")
    }
    profile.last_verified_at = datetime.now(timezone.utc)
    profile.last_evidence_summary = {
        "trusted": trusted,
        "evidence": evidence_status,
        "trust_capabilities": capability_summary
        or build_trust_capability_summary(
            session,
            node,
            profile,
            [record for record in evidence if hasattr(record, "evidence_type")],
        ),
        "reason": decision_data.get("reason") or trust_agent_result.get("reason") or "",
        "source": (
            trust_agent_result.get("source")
            or trust_agent_result.get("trust_agent_type")
            or ""
        ),
    }
    if not trust_managed:
        profile.registration_status = REGISTRATION_UNMANAGED
    elif trusted:
        profile.registration_status = REGISTRATION_VERIFIED
    else:
        profile.registration_status = REGISTRATION_UNTRUSTED


def _decision_capabilities(settings: Settings, profile: TrustedNodeProfile) -> dict[str, bool]:
    global_capabilities = dict(settings.effective_trust_capabilities)
    profile_capabilities = dict(profile.capabilities or {})
    if not profile_capabilities:
        return global_capabilities
    return {
        "boot": bool(
            global_capabilities.get("boot")
            and profile_capabilities.get(CAPABILITY_TRUSTED_BOOT) is True
        ),
        "ima": bool(
            global_capabilities.get("ima")
            and (
                profile_capabilities.get(CAPABILITY_IMA_RUNTIME) is True
                or profile_capabilities.get(CAPABILITY_TPCM_DYNAMIC_MEASUREMENT) is True
            )
        ),
        "evm": bool(
            global_capabilities.get("evm")
            and profile_capabilities.get(CAPABILITY_EVM) is True
        ),
        "openstack_service": bool(global_capabilities.get("openstack_service")),
    }


def _runtime_capability(profile: TrustedNodeProfile) -> str:
    capabilities = dict(profile.capabilities or {})
    if (
        capabilities.get(CAPABILITY_TPCM_DYNAMIC_MEASUREMENT) is True
        and capabilities.get(CAPABILITY_IMA_RUNTIME) is not True
    ):
        return CAPABILITY_TPCM_DYNAMIC_MEASUREMENT
    return CAPABILITY_IMA_RUNTIME
