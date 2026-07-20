"""OpenTCSM/Hygon TPCM evidence ingestion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from keylime_openstack.config import Settings
from keylime_openstack.constants import PROVIDER_OPENTCSM
from keylime_openstack.models import ComputeNode, EvidenceRecord


def opentcsm_report_to_evidence(
    node: ComputeNode,
    report: dict[str, Any],
    settings: Settings,
) -> list[EvidenceRecord]:
    collected_at = _coerce_datetime(report.get("collected_at")) or datetime.now(timezone.utc)
    valid_until = collected_at + timedelta(seconds=settings.opentcsm_evidence_fresh_seconds)
    payload = {
        "trust_agent": "opentcsm_tpcm",
        "trust_root": report.get("trust_root") or "Hygon TPCM",
        "agent_name": report.get("agent_name") or "OpenTCSM",
        "agent_version": report.get("agent_version") or "",
        "report_type": report.get("report_type") or "tpcm",
        "trusted": report.get("trusted"),
        "raw": report.get("raw") or {},
        "errors": report.get("errors") or [],
    }

    boot_status = _status(
        report.get("boot_status"),
        overall=report.get("trusted"),
        default="unknown",
    )
    runtime_status = _status(
        report.get("dynamic_measurement_status")
        if report.get("dynamic_measurement_status") is not None
        else report.get("runtime_status")
        if report.get("runtime_status") is not None
        else report.get("ima_status"),
        overall=report.get("trusted"),
        default="unknown",
    )
    records = [
        EvidenceRecord(
            node_id=node.id,
            provider=PROVIDER_OPENTCSM,
            evidence_type="boot",
            collected_at=collected_at,
            valid_until=valid_until,
            status=boot_status,
            summary=_summary("TPCM trusted boot", boot_status, report),
            payload=payload,
        ),
        EvidenceRecord(
            node_id=node.id,
            provider=PROVIDER_OPENTCSM,
            evidence_type="runtime",
            collected_at=collected_at,
            valid_until=valid_until,
            status=runtime_status,
            summary=_summary("TPCM dynamic measurement", runtime_status, report),
            payload=payload,
        ),
    ]

    evm_status = _status(report.get("evm_status"), overall=None, default="")
    if evm_status:
        records.append(
            EvidenceRecord(
                node_id=node.id,
                provider=PROVIDER_OPENTCSM,
                evidence_type="evm",
                collected_at=collected_at,
                valid_until=valid_until,
                status=evm_status,
                summary=_summary("TPCM EVM/Appraisal", evm_status, report),
                payload=payload,
            )
        )
    return records


def _status(value: object, *, overall: object, default: str) -> str:
    if value is None or value == "":
        if isinstance(overall, bool):
            return "pass" if overall else "fail"
        return default
    if isinstance(value, bool):
        return "pass" if value else "fail"
    normalized = str(value).strip().lower().replace("_", "-")
    if normalized in {"pass", "passed", "ok", "true", "trusted", "success", "valid"}:
        return "pass"
    if normalized in {"fail", "failed", "false", "untrusted", "error", "invalid"}:
        return "fail"
    if normalized in {"missing", "not-found", "not-found"}:
        return "missing"
    return "unknown"


def _summary(label: str, status: str, report: dict[str, Any]) -> str:
    summary = str(report.get("summary") or "").strip()
    if summary:
        return f"{label} {status}: {summary}"
    return f"{label} {status} from OpenTCSM/Hygon TPCM"


def _coerce_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
