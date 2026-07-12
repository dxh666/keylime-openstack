"""Worker synchronization workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.models import (
    AuditEvent,
    ComputeNode,
    EvidenceRecord,
    OpenStackState,
    TrustDecision,
)
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.decision import evaluate_trust
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.openstack import OpenStackClient


class TrustSyncService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.openstack = OpenStackClient(settings)
        self.keylime = KeylimeClient(settings)

    def run_once(self) -> dict[str, object]:
        ensure_default_environment(self.session)
        self.session.flush()
        nodes = self.session.scalars(
            select(ComputeNode)
            .where(ComputeNode.role == "compute")
            .where(ComputeNode.enabled.is_(True))
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
            service = services_by_host.get(node.hypervisor_name) or services_by_host.get(
                node.hostname
            )
            if not service:
                continue
            self.session.add(
                OpenStackState(
                    node_id=node.id,
                    service_binary=str(
                        service.get("binary") or service.get("Binary") or "nova-compute"
                    ),
                    service_status=str(
                        service.get("status") or service.get("Status") or "unknown"
                    ).lower(),
                    service_state=str(
                        service.get("state") or service.get("State") or "unknown"
                    ).lower(),
                    raw=_json_safe(service),
                )
            )

    def sync_node(self, node: ComputeNode) -> dict[str, object]:
        keylime_result = self.collect_keylime_evidence(node)
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
            "keylime": keylime_result,
        }

    def collect_keylime_evidence(self, node: ComputeNode) -> dict[str, object]:
        if not node.keylime_agent_uuid:
            self.session.add(
                AuditEvent(
                    event_type="keylime_evidence_collect",
                    target=node.hostname,
                    severity="warning",
                    message="missing Keylime agent UUID",
                    event_details={},
                )
            )
            return {"status": "skipped", "reason": "missing-agent-uuid"}

        try:
            status = self.keylime.read_agent_status(node.keylime_agent_uuid)
        except Exception as exc:  # pragma: no cover - deployment-specific API boundary
            self.session.add(
                AuditEvent(
                    event_type="keylime_evidence_collect",
                    target=node.hostname,
                    severity="error",
                    message=str(exc),
                    event_details={"agent_uuid": node.keylime_agent_uuid},
                )
            )
            return {"status": "error", "reason": str(exc)}

        if status.get("ip") and not node.keylime_agent_ip:
            node.keylime_agent_ip = str(status["ip"])

        records = keylime_status_to_evidence(node, status, self.settings)
        for record in records:
            self.session.add(record)
        return {
            "status": "collected",
            "source": str(status.get("_source") or "unknown"),
            "evidence": {record.evidence_type: record.status for record in records},
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


def keylime_status_to_evidence(
    node: ComputeNode,
    status: dict[str, Any],
    settings: Settings,
) -> list[EvidenceRecord]:
    attestation_status = str(status.get("attestation_status") or "").upper()
    operational_state = str(status.get("operational_state") or "unknown")
    last_event_id = str(status.get("last_event_id") or "")
    has_runtime_policy = _truthy(status.get("has_runtime_policy"))
    source = str(status.get("_source") or "keylime")
    valid_until = _attestation_valid_until(status, settings, attestation_status)
    payload = _keylime_payload(status)

    if attestation_status == "PASS":
        boot_status = "pass"
        runtime_status = "pass" if has_runtime_policy else "missing"
    elif attestation_status == "FAIL":
        boot_status = "fail"
        runtime_status = (
            "fail" if has_runtime_policy or last_event_id.startswith("ima.") else "unknown"
        )
    else:
        boot_status = "unknown"
        runtime_status = "unknown" if has_runtime_policy else "missing"

    evm_status, evm_summary = _evm_status_from_keylime(status)

    return [
        EvidenceRecord(
            node_id=node.id,
            provider="keylime",
            evidence_type="boot",
            valid_until=valid_until,
            status=boot_status,
            summary=_summary(
                "TPM quote",
                boot_status,
                source,
                operational_state,
                last_event_id,
            ),
            payload=payload,
        ),
        EvidenceRecord(
            node_id=node.id,
            provider="keylime",
            evidence_type="runtime",
            valid_until=valid_until,
            status=runtime_status,
            summary=_summary(
                "IMA runtime policy",
                runtime_status,
                source,
                operational_state,
                last_event_id,
            ),
            payload=payload,
        ),
        EvidenceRecord(
            node_id=node.id,
            provider="keylime",
            evidence_type="evm",
            valid_until=valid_until,
            status=evm_status,
            summary=evm_summary,
            payload=payload,
        ),
    ]


def _attestation_valid_until(
    status: dict[str, Any],
    settings: Settings,
    attestation_status: str,
) -> datetime:
    key = "last_successful_attestation" if attestation_status == "PASS" else "last_received_quote"
    timestamp = _coerce_epoch(status.get(key)) or _coerce_epoch(
        status.get("last_successful_attestation")
    )
    if timestamp:
        return datetime.fromtimestamp(timestamp, timezone.utc) + timedelta(
            seconds=settings.attestation_fresh_seconds
        )
    return datetime.now(timezone.utc) + timedelta(seconds=settings.attestation_fresh_seconds)


def _coerce_epoch(value: Any) -> int | None:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    return timestamp if timestamp > 0 else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "enabled", "pass"}


def _evm_status_from_keylime(status: dict[str, Any]) -> tuple[str, str]:
    for key in (
        "evm_status",
        "evm_validation",
        "evm_signature_status",
        "keyring_status",
        "ima_appraisal_status",
    ):
        if key not in status:
            continue
        value = str(status.get(key) or "").strip().lower()
        if value in {"pass", "passed", "valid", "verified", "trusted", "success"}:
            return "pass", f"EVM/keyring verification reported pass by Keylime field {key}"
        if value in {"fail", "failed", "invalid", "untrusted", "error"}:
            return "fail", f"EVM/keyring verification reported fail by Keylime field {key}"
    return "missing", "EVM/keyring verification not reported by current Keylime status payload"


def _summary(
    label: str,
    status: str,
    source: str,
    operational_state: str,
    last_event_id: str,
) -> str:
    event = f", last_event={last_event_id}" if last_event_id else ""
    return f"{label} {status} from {source} (state={operational_state}{event})"


def _keylime_payload(status: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = (
        "_source",
        "_adapter_errors",
        "operational_state",
        "ip",
        "port",
        "tpm_policy",
        "meta_data",
        "has_mb_refstate",
        "has_runtime_policy",
        "accept_tpm_hash_algs",
        "hash_alg",
        "enc_alg",
        "sign_alg",
        "verifier_id",
        "verifier_ip",
        "verifier_port",
        "severity_level",
        "last_event_id",
        "attestation_count",
        "last_received_quote",
        "last_successful_attestation",
        "attestation_status",
        "attestation_period",
        "maximum_attestation_interval",
    )
    return {key: _json_safe(status[key]) for key in allowed_keys if key in status}
