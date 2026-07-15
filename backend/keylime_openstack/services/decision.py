"""Trust decision engine.

The decision engine keeps OpenStack scheduling semantics separate from evidence
collection. Keylime and future DIM providers write evidence into the database;
this module converts those records into desired Placement traits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    TRAIT_BOOT_TRUSTED,
    TRAIT_LEGACY_ATTESTED,
    TRAIT_RUNTIME_TRUSTED,
    TRAIT_TRUSTED,
)
from keylime_openstack.models import EvidenceRecord, OpenStackState


def _fresh(record: EvidenceRecord | None) -> bool:
    if not record:
        return False
    if record.valid_until is None:
        return True
    return record.valid_until >= datetime.now(timezone.utc)


def _valid_until(record: EvidenceRecord | None) -> str | None:
    if not record or record.valid_until is None:
        return None
    return record.valid_until.isoformat()


def _waiting_reason(label: str, record: EvidenceRecord | None, fresh: bool) -> str:
    if not record:
        return label
    if record.status == "pass" and not fresh:
        return f"{label}_stale"
    if record.status in {"fail", "unknown", "missing"}:
        return f"{label}_{record.status}"
    return label


def latest_by_type(records: list[EvidenceRecord]) -> dict[str, EvidenceRecord]:
    latest: dict[str, EvidenceRecord] = {}
    for record in sorted(records, key=lambda item: item.collected_at):
        latest[record.evidence_type] = record
    return latest


def evaluate_trust(
    *,
    evidence: list[EvidenceRecord],
    openstack_state: OpenStackState | None,
    settings: Settings,
) -> dict[str, Any]:
    """Return a normalized trust decision for one compute host."""

    latest = latest_by_type(evidence)
    boot = latest.get("boot")
    runtime = latest.get("runtime")
    evm = latest.get("evm")

    boot_fresh = _fresh(boot)
    runtime_fresh = _fresh(runtime)
    evm_fresh = _fresh(evm)
    boot_trusted = bool(boot and boot.status == "pass" and boot_fresh)
    runtime_keylime = bool(runtime and runtime.status == "pass" and runtime_fresh)
    evm_trusted = bool(evm and evm.status == "pass" and evm_fresh)
    trust_policy_mode = settings.normalized_trust_policy_mode
    evm_required = trust_policy_mode == "evm-required"
    runtime_trusted = runtime_keylime and (evm_trusted if evm_required else True)

    service_ok = bool(
        openstack_state
        and openstack_state.service_status == "enabled"
        and openstack_state.service_state == "up"
    )

    trusted = boot_trusted and runtime_trusted and service_ok
    desired_traits: list[str] = []
    if boot_trusted:
        desired_traits.append(TRAIT_BOOT_TRUSTED)
    if runtime_trusted:
        desired_traits.append(TRAIT_RUNTIME_TRUSTED)
    if trusted:
        desired_traits.append(TRAIT_TRUSTED)
        if settings.legacy_trait_enabled:
            desired_traits.append(TRAIT_LEGACY_ATTESTED)

    missing = []
    if not boot_trusted:
        missing.append(_waiting_reason("boot", boot, boot_fresh))
    if not runtime_keylime:
        missing.append(_waiting_reason("ima", runtime, runtime_fresh))
    if evm_required and not evm_trusted:
        missing.append(_waiting_reason("evm", evm, evm_fresh))
    if not service_ok:
        missing.append("openstack-service")

    reason = "TRUSTED" if trusted else "WAITING_FOR_" + "_".join(missing).upper()
    return {
        "boot_trusted": boot_trusted,
        "runtime_trusted": runtime_trusted,
        "trusted": trusted,
        "reason": reason,
        "desired_traits": desired_traits,
        "evidence_refs": {
            "boot": boot.id if boot else None,
            "runtime": runtime.id if runtime else None,
            "evm": evm.id if evm else None,
        },
        "details": {
            "boot_status": boot.status if boot else "missing",
            "runtime_status": runtime.status if runtime else "missing",
            "evm_status": evm.status if evm else "missing",
            "boot_fresh": boot_fresh,
            "runtime_fresh": runtime_fresh,
            "evm_fresh": evm_fresh,
            "boot_valid_until": _valid_until(boot),
            "runtime_valid_until": _valid_until(runtime),
            "evm_valid_until": _valid_until(evm),
            "evm_required": evm_required,
            "trust_policy_mode": trust_policy_mode,
            "service_status": openstack_state.service_status if openstack_state else "missing",
            "service_state": openstack_state.service_state if openstack_state else "missing",
        },
    }
