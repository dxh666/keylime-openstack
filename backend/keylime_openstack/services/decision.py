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
    TRUST_AGENT_UNMANAGED,
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


def unmanaged_trust_decision(openstack_state: OpenStackState | None) -> dict[str, Any]:
    """Return a trust decision for a compute node outside trusted-agent management."""

    return {
        "boot_trusted": False,
        "runtime_trusted": False,
        "trusted": False,
        "reason": "TRUST_AGENT_UNMANAGED",
        "desired_traits": [],
        "evidence_refs": {
            "boot": None,
            "runtime": None,
            "evm": None,
        },
        "details": {
            "boot_status": TRUST_AGENT_UNMANAGED,
            "runtime_status": TRUST_AGENT_UNMANAGED,
            "evm_status": TRUST_AGENT_UNMANAGED,
            "boot_fresh": False,
            "runtime_fresh": False,
            "evm_fresh": False,
            "boot_valid_until": None,
            "runtime_valid_until": None,
            "evm_valid_until": None,
            "trust_management_status": TRUST_AGENT_UNMANAGED,
            "trust_managed": False,
            "service_status": openstack_state.service_status if openstack_state else "missing",
            "service_state": openstack_state.service_state if openstack_state else "missing",
        },
    }


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
    capabilities = settings.effective_trust_capabilities
    evm_required = capabilities["evm"]
    runtime_trusted = runtime_keylime

    service_ok = bool(
        openstack_state
        and openstack_state.service_status == "enabled"
        and openstack_state.service_state == "up"
    )

    capability_status = {
        "boot": boot_trusted,
        "ima": runtime_keylime,
        "evm": evm_trusted,
        "openstack_service": service_ok,
    }
    enabled_capabilities = [
        name for name, enabled in capabilities.items() if enabled
    ]
    enabled_keylime_capabilities = [
        name for name in ("boot", "ima", "evm") if capabilities[name]
    ]
    trusted = bool(enabled_keylime_capabilities) and all(
        capability_status[name] for name in enabled_capabilities
    )
    desired_traits: list[str] = []
    if capabilities["boot"] and boot_trusted:
        desired_traits.append(TRAIT_BOOT_TRUSTED)
    if capabilities["ima"] and runtime_trusted:
        desired_traits.append(TRAIT_RUNTIME_TRUSTED)
    if trusted:
        desired_traits.append(TRAIT_TRUSTED)
        if settings.legacy_trait_enabled:
            desired_traits.append(TRAIT_LEGACY_ATTESTED)

    missing = []
    if capabilities["boot"] and not boot_trusted:
        missing.append(_waiting_reason("boot", boot, boot_fresh))
    if capabilities["ima"] and not runtime_keylime:
        missing.append(_waiting_reason("ima", runtime, runtime_fresh))
    if capabilities["evm"] and not evm_trusted:
        missing.append(_waiting_reason("evm", evm, evm_fresh))
    if capabilities["openstack_service"] and not service_ok:
        missing.append("openstack-service")

    if trusted:
        reason = "TRUSTED"
    elif not enabled_keylime_capabilities:
        reason = "NO_KEYLIME_TRUST_CAPABILITY_ENABLED"
    else:
        reason = "WAITING_FOR_" + "_".join(missing).upper()
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
            "trust_capabilities": capabilities,
            "enabled_trust_capabilities": enabled_capabilities,
            "enabled_keylime_trust_capabilities": enabled_keylime_capabilities,
            "capability_status": capability_status,
            "service_status": openstack_state.service_status if openstack_state else "missing",
            "service_state": openstack_state.service_state if openstack_state else "missing",
        },
    }
