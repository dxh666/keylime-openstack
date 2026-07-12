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

    boot_trusted = bool(boot and boot.status == "pass" and _fresh(boot))
    runtime_keylime = bool(runtime and runtime.status == "pass" and _fresh(runtime))
    evm_trusted = bool(evm and evm.status == "pass" and _fresh(evm))
    runtime_trusted = runtime_keylime and evm_trusted

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
        missing.append("boot")
    if not runtime_keylime:
        missing.append("ima")
    if not evm_trusted:
        missing.append("evm")
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
            "service_status": openstack_state.service_status if openstack_state else "missing",
            "service_state": openstack_state.service_state if openstack_state else "missing",
        },
    }
