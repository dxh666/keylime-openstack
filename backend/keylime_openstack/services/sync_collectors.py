"""Evidence and state collectors used by the sync workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    PROVIDER_KEYLIME,
    PROVIDER_OPENTCSM,
    TRUST_AGENT_KEYLIME,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_AGENT_UNMANAGED,
)
from keylime_openstack.models import ComputeNode, EvidenceRecord, OpenStackState
from keylime_openstack.services.audit import record_audit_event
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.openstack import OpenStackClient
from keylime_openstack.services.opentcsm_collect import OpenTcsmCollector
from keylime_openstack.services.trust_agents import (
    node_trust_agent_name,
    node_trust_agent_type,
    node_trust_managed,
    node_trusted_root,
    node_trusted_root_type,
)


class OpenStackStateCollector:
    def __init__(self, session: Session, openstack: OpenStackClient) -> None:
        self.session = session
        self.openstack = openstack

    def refresh(self, nodes: list[ComputeNode]) -> None:
        """Collect nova-compute service state before evaluating trust."""

        try:
            services = self.openstack.list_compute_services()
        except Exception as exc:  # pragma: no cover - deployment-specific API boundary
            error = str(exc)
            record_audit_event(
                self.session,
                event_type="openstack_state_refresh",
                target="nova-compute",
                severity="error",
                message=error,
                event_details={"adapter": "openstacksdk"},
            )
            for node in nodes:
                self.session.add(_openstack_collection_error_state(node, error))
            return

        services_by_host: dict[str, dict[str, Any]] = {}
        for service in services:
            for key in _host_keys(_service_field(service, "host")):
                services_by_host.setdefault(key, service)
        for node in nodes:
            service = _service_for_node(services_by_host, node)
            if not service:
                self.session.add(_missing_openstack_service_state(node, services_by_host))
                continue
            self.session.add(_openstack_service_state(node, service))


def _service_for_node(
    services_by_host: dict[str, dict[str, Any]],
    node: ComputeNode,
) -> dict[str, Any] | None:
    for key in _host_keys(node.hypervisor_name, node.hostname):
        service = services_by_host.get(key)
        if service:
            return service
    return None


def _openstack_service_state(node: ComputeNode, service: dict[str, Any]) -> OpenStackState:
    return OpenStackState(
        node_id=node.id,
        service_binary=str(_service_field(service, "binary") or "nova-compute"),
        service_status=str(_service_field(service, "status") or "unknown").lower(),
        service_state=str(_service_field(service, "state") or "unknown").lower(),
        raw=_json_safe(service),
    )


def _missing_openstack_service_state(
    node: ComputeNode,
    services_by_host: dict[str, dict[str, Any]],
) -> OpenStackState:
    known_hosts = sorted(
        {
            str(_service_field(service, "host") or "")
            for service in services_by_host.values()
            if _service_field(service, "host")
        }
    )
    return OpenStackState(
        node_id=node.id,
        service_binary="nova-compute",
        service_status="missing",
        service_state="down",
        raw={
            "source": "openstack compute service list",
            "reason": "nova-compute service not found in current refresh",
            "hostname": node.hostname,
            "hypervisor_name": node.hypervisor_name,
            "known_service_hosts": known_hosts,
        },
    )


def _openstack_collection_error_state(node: ComputeNode, error: str) -> OpenStackState:
    return OpenStackState(
        node_id=node.id,
        service_binary="nova-compute",
        service_status="unknown",
        service_state="unknown",
        raw={
            "source": "openstack compute service list",
            "reason": "openstack service collection failed",
            "hostname": node.hostname,
            "hypervisor_name": node.hypervisor_name,
            "error": error,
        },
    )


def _service_field(service: dict[str, Any], name: str) -> Any:
    for key, value in service.items():
        if str(key).lower() == name:
            return value
    return None


def _host_keys(*values: object) -> set[str]:
    keys: set[str] = set()
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        keys.add(text)
        if "." in text:
            keys.add(text.split(".", 1)[0])
    return keys


class TrustEvidenceCollector:
    def __init__(self, session: Session, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.keylime = KeylimeClient(settings)

    def collect_for_node(self, node: ComputeNode) -> dict[str, object]:
        agent_type = node_trust_agent_type(node, self.settings)
        if agent_type == TRUST_AGENT_UNMANAGED:
            return self.collect_unmanaged_node(node)
        if agent_type == TRUST_AGENT_KEYLIME:
            return self.collect_tpm_evidence(node)
        return self.collect_external_trust_root_evidence(node)

    def collect_unmanaged_node(self, node: ComputeNode) -> dict[str, object]:
        details = self._result_base(node)
        audit_details = {
            **details,
            "log_type": "node_management",
            "subject_name": "可信代理",
            "object_name": node.hostname,
            "operation": "未纳管",
            "result": "提醒",
        }
        record_audit_event(
            self.session,
            event_type="trust_agent_evidence_collect",
            target=node.hostname,
            severity="warning",
            message="TRUST_AGENT_UNMANAGED",
            event_details=audit_details,
        )
        return {
            **details,
            "status": "unmanaged",
            "reason": "trusted-root-agent-unmanaged",
            "evidence": {},
        }

    def collect_external_trust_root_evidence(self, node: ComputeNode) -> dict[str, object]:
        agent_type = node_trust_agent_type(node, self.settings)
        active_collection = None
        latest_opentcsm = latest_evidence_record(
            self.session,
            node,
            provider=PROVIDER_OPENTCSM,
        )
        if (
            agent_type == TRUST_AGENT_OPENTCSM_TPCM
            and self.settings.opentcsm_active_collect_enabled
            and _opentcsm_collection_due(latest_opentcsm, self.settings)
        ):
            active_collection = self._collect_tpcm_evidence(node)

        records = latest_evidence_for_decision(self.session, node)
        evidence = {record.evidence_type: record.status for record in records}
        if not records:
            record_audit_event(
                self.session,
                event_type="trust_agent_evidence_collect",
                target=node.hostname,
                severity="warning",
                message=f"no evidence reported by {agent_type}",
                event_details=self._result_base(node),
            )
            result = {
                **self._result_base(node),
                "status": "pending",
                "reason": "waiting-for-external-trust-agent-evidence",
                "evidence": {},
            }
            if active_collection is not None:
                result["active_collection"] = active_collection
            return result

        result = {
            **self._result_base(node),
            "status": "collected",
            "evidence": evidence,
        }
        if active_collection is not None:
            result["active_collection"] = active_collection
        return result

    def _collect_tpcm_evidence(self, node: ComputeNode) -> dict[str, object]:
        try:
            result = OpenTcsmCollector(self.session, self.settings).collect(node)
            return {
                "status": "collected",
                "source": TRUST_AGENT_OPENTCSM_TPCM,
                "trusted": result.get("trusted"),
                "boot_status": result.get("boot_status"),
                "runtime_status": result.get("dynamic_measurement_status"),
            }
        except Exception as exc:  # pragma: no cover - remote execution boundary
            now = datetime.now(timezone.utc)
            valid_until = now + timedelta(
                seconds=max(self.settings.opentcsm_collect_interval_seconds, 5)
            )
            error = str(exc)
            payload = {
                "trust_agent": TRUST_AGENT_OPENTCSM_TPCM,
                "trust_root": node_trusted_root(node, self.settings),
                "agent_name": node_trust_agent_name(node, self.settings),
                "communication": "error",
                "error": error,
            }
            for evidence_type, label in (
                ("boot", "TPCM trusted boot"),
                ("runtime", "TPCM dynamic measurement"),
            ):
                self.session.add(
                    EvidenceRecord(
                        node_id=node.id,
                        provider=PROVIDER_OPENTCSM,
                        evidence_type=evidence_type,
                        collected_at=now,
                        valid_until=valid_until,
                        status="unknown",
                        summary=f"{label} unknown: {error}",
                        payload=payload,
                    )
                )
            record_audit_event(
                self.session,
                event_type="opentcsm_evidence_collect",
                target=node.hostname,
                severity="error",
                message=error,
                event_details={
                    "provider": PROVIDER_OPENTCSM,
                    "source": "ansible",
                    "communication": "error",
                },
            )
            self.session.flush()
            return {
                "status": "error",
                "source": TRUST_AGENT_OPENTCSM_TPCM,
                "reason": error,
            }

    def collect_tpm_evidence(self, node: ComputeNode) -> dict[str, object]:
        if not node.keylime_agent_uuid:
            record_audit_event(
                self.session,
                event_type="keylime_evidence_collect",
                target=node.hostname,
                severity="warning",
                message="missing Keylime agent UUID",
                event_details=self._result_base(node),
            )
            return {
                **self._result_base(node),
                "status": "skipped",
                "reason": "missing-agent-uuid",
            }

        try:
            status = self.keylime.read_agent_status(node.keylime_agent_uuid)
        except Exception as exc:  # pragma: no cover - deployment-specific API boundary
            record_audit_event(
                self.session,
                event_type="keylime_evidence_collect",
                target=node.hostname,
                severity="error",
                message=str(exc),
                event_details={
                    **self._result_base(node),
                    "agent_uuid": node.keylime_agent_uuid,
                },
            )
            return {
                **self._result_base(node),
                "status": "error",
                "reason": str(exc),
            }

        if status.get("ip") and not node.keylime_agent_ip:
            node.keylime_agent_ip = str(status["ip"])

        records = keylime_status_to_evidence(node, status, self.settings)
        for record in records:
            self.session.add(record)
        return {
            **self._result_base(node),
            "status": "collected",
            "source": str(status.get("_source") or PROVIDER_KEYLIME),
            "evidence": {record.evidence_type: record.status for record in records},
        }

    def _result_base(self, node: ComputeNode) -> dict[str, object]:
        agent_type = node_trust_agent_type(node, self.settings)
        return {
            "trust_agent_type": agent_type,
            "trust_agent_name": node_trust_agent_name(node, self.settings),
            "trust_managed": node_trust_managed(node, self.settings),
            "trusted_root_type": node_trusted_root_type(node, self.settings),
            "trusted_root": node_trusted_root(node, self.settings),
        }


def latest_evidence_for_decision(session: Session, node: ComputeNode) -> list[EvidenceRecord]:
    """Return the latest evidence per type for a node."""

    records = []
    for evidence_type in ("boot", "runtime", "evm"):
        record = session.scalars(
            select(EvidenceRecord)
            .where(EvidenceRecord.node_id == node.id)
            .where(EvidenceRecord.evidence_type == evidence_type)
            .order_by(EvidenceRecord.collected_at.desc(), EvidenceRecord.id.desc())
            .limit(1)
        ).first()
        if record:
            records.append(record)
    return records


def latest_evidence_record(
    session: Session,
    node: ComputeNode,
    *,
    provider: str,
) -> EvidenceRecord | None:
    return session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.node_id == node.id)
        .where(EvidenceRecord.provider == provider)
        .order_by(EvidenceRecord.collected_at.desc(), EvidenceRecord.id.desc())
        .limit(1)
    ).first()


def _opentcsm_collection_due(record: EvidenceRecord | None, settings: Settings) -> bool:
    if not record:
        return True
    collected_at = record.collected_at
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    interval = max(settings.opentcsm_collect_interval_seconds, 5)
    return collected_at + timedelta(seconds=interval) <= datetime.now(timezone.utc)


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
    active_last_event_id = _active_last_event_id(attestation_status, last_event_id)
    has_runtime_policy = _truthy(status.get("has_runtime_policy"))
    source = str(status.get("_source") or PROVIDER_KEYLIME)
    valid_until = _attestation_valid_until(status, settings, attestation_status)
    payload = _keylime_payload(status)

    if attestation_status == "PASS":
        boot_status = "pass"
        runtime_status = "pass" if has_runtime_policy else "missing"
    elif attestation_status == "FAIL":
        boot_status, runtime_status = _failed_attestation_evidence_status(
            last_event_id=last_event_id,
            has_runtime_policy=has_runtime_policy,
        )
    else:
        boot_status = "unknown"
        runtime_status = "unknown" if has_runtime_policy else "missing"

    records = [
        EvidenceRecord(
            node_id=node.id,
            provider=PROVIDER_KEYLIME,
            evidence_type="boot",
            valid_until=valid_until,
            status=boot_status,
            summary=_summary(
                "TPM quote",
                boot_status,
                source,
                operational_state,
                active_last_event_id,
            ),
            payload=payload,
        ),
        EvidenceRecord(
            node_id=node.id,
            provider=PROVIDER_KEYLIME,
            evidence_type="runtime",
            valid_until=valid_until,
            status=runtime_status,
            summary=_summary(
                "IMA runtime policy",
                runtime_status,
                source,
                operational_state,
                active_last_event_id,
            ),
            payload=payload,
        ),
    ]
    evm = _evm_status_from_keylime(status)
    if evm:
        evm_status, evm_summary = evm
        records.append(
            EvidenceRecord(
                node_id=node.id,
                provider=PROVIDER_KEYLIME,
                evidence_type="evm",
                valid_until=valid_until,
                status=evm_status,
                summary=evm_summary,
                payload=payload,
            )
        )
    return records


def _failed_attestation_evidence_status(
    *,
    last_event_id: str,
    has_runtime_policy: bool,
) -> tuple[str, str]:
    event = last_event_id.lower()
    runtime_unknown = "unknown" if has_runtime_policy else "missing"

    if event.startswith("ima."):
        return "pass", "fail"

    if event.startswith("internal.verifier."):
        return "unknown", runtime_unknown

    if event.startswith(("measured_boot.", "pcr.", "quote.", "tpm.")):
        return "fail", runtime_unknown

    return "fail", runtime_unknown


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


def _active_last_event_id(attestation_status: str, last_event_id: str) -> str:
    if attestation_status == "PASS":
        return ""
    return last_event_id


def _evm_status_from_keylime(status: dict[str, Any]) -> tuple[str, str] | None:
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
    return None


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
