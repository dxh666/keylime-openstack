"""Host-side IMA appraisal and EVM evidence evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from keylime_openstack.config import Settings
from keylime_openstack.models import ComputeNode, EvidenceRecord


def host_integrity_report_to_evidence(
    node: ComputeNode,
    report: dict[str, Any],
    settings: Settings,
) -> EvidenceRecord:
    """Convert a host probe report into conservative EVM evidence."""

    evaluation = evaluate_host_integrity_report(report)
    now = datetime.now(timezone.utc)
    return EvidenceRecord(
        node_id=node.id,
        provider="host-integrity-probe",
        evidence_type="evm",
        collected_at=now,
        valid_until=now + timedelta(seconds=settings.host_integrity_fresh_seconds),
        status=evaluation["status"],
        summary=evaluation["summary"],
        payload={
            "evaluation": evaluation,
            "report": _json_safe(report),
        },
    )


def evaluate_host_integrity_report(report: dict[str, Any]) -> dict[str, Any]:
    cmdline = str(report.get("cmdline") or "")
    policy_lines = _string_list(report.get("ima_policy"))
    policy_error = str(report.get("ima_policy_error") or "")
    ima_key_count = _key_count(report.get("ima_keyring"))
    evm_key_count = _key_count(report.get("evm_keyring"))
    dmesg_lines = _string_list(report.get("dmesg_integrity_tail"))
    templates = _dict(report.get("ima_measurement_templates"))
    securityfs_mounted = bool(report.get("securityfs_mounted"))
    xattrs = _dict(report.get("xattrs"))

    appraise_enabled = "ima_appraise=" in cmdline or any(
        "appraise" in line for line in policy_lines
    )
    policy_readable = bool(policy_lines) and not policy_error
    evm_initialized = any("evm: Initialising EVM" in line for line in dmesg_lines)
    ima_sig_seen = int(templates.get("ima-sig") or 0) > 0
    evm_xattr_seen = any(_dict(value).get("security.evm") for value in xattrs.values())
    ima_xattr_seen = any(_dict(value).get("security.ima") for value in xattrs.values())
    severe_errors = [
        line
        for line in dmesg_lines
        if _contains_any(
            line.lower(),
            (
                "appraisal failed",
                "signature verification failed",
                "integrity: failed",
                "evm: verification failed",
            ),
        )
    ]

    checks = {
        "securityfs_mounted": securityfs_mounted,
        "ima_policy_readable": policy_readable,
        "ima_appraisal_enabled": appraise_enabled,
        "ima_keyring_populated": ima_key_count > 0,
        "evm_keyring_populated": evm_key_count > 0,
        "evm_initialized": evm_initialized,
        "ima_sig_measurements_seen": ima_sig_seen,
        "ima_xattr_seen": ima_xattr_seen,
        "evm_xattr_seen": evm_xattr_seen,
        "severe_integrity_errors": severe_errors,
    }

    if all(
        (
            securityfs_mounted,
            appraise_enabled,
            ima_key_count > 0,
            evm_key_count > 0,
            evm_initialized,
            ima_xattr_seen,
            evm_xattr_seen,
            not severe_errors,
        )
    ):
        status = "pass"
        summary = "IMA appraisal, EVM keyring, and signed xattrs are present"
    elif appraise_enabled or ima_sig_seen:
        status = "fail"
        summary = _failure_summary(checks)
    else:
        status = "missing"
        summary = "IMA appraisal/EVM signature verification is not enabled"

    return {
        "status": status,
        "summary": summary,
        "checks": checks,
    }


def _failure_summary(checks: dict[str, Any]) -> str:
    missing = []
    if not checks["ima_appraisal_enabled"]:
        missing.append("ima-appraise-policy")
    if not checks["ima_keyring_populated"]:
        missing.append("ima-keyring")
    if not checks["evm_keyring_populated"]:
        missing.append("evm-keyring")
    if not checks["evm_initialized"]:
        missing.append("evm-init")
    if not checks["ima_xattr_seen"]:
        missing.append("security.ima-xattr")
    if not checks["evm_xattr_seen"]:
        missing.append("security.evm-xattr")
    if checks["severe_integrity_errors"]:
        missing.append("dmesg-integrity-errors")
    return "IMA appraisal/EVM evidence incomplete: " + ",".join(missing)


def _key_count(value: Any) -> int:
    if isinstance(value, dict):
        if "key_count" in value:
            try:
                return int(value["key_count"])
            except (TypeError, ValueError):
                return 0
        lines = _string_list(value.get("lines"))
        return len([line for line in lines if line.strip() and "keyring is empty" not in line])
    lines = _string_list(value)
    return len([line for line in lines if line.strip() and "keyring is empty" not in line])


def _contains_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in value for pattern in patterns)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [line for line in value.splitlines() if line]
    return []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
