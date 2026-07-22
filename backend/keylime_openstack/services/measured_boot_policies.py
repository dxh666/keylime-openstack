"""Measured Boot policy rendering helpers."""

from __future__ import annotations

import re
from typing import Any

from keylime_openstack.models import TrustPolicy

__all__ = [
    "_event_log_fallback_mode",
    "_fallback_pcrs",
    "_measured_boot_pcrs",
    "_parse_tpm2_pcrread_sha256",
    "_tpm_policy_from_pcrs",
]

def _event_log_fallback_mode(policy: TrustPolicy) -> str:
    value = str(policy.content.get("event_log_fallback") or "pcr_quote")
    normalized = value.strip().lower().replace("-", "_")
    return "pcr_quote" if normalized in {"pcr_quote", "tpm_pcr", "tpm_pcr_quote"} else "none"



def _measured_boot_pcrs(policy: TrustPolicy) -> list[int]:
    raw = policy.content.get("pcrs") or list(range(8))
    try:
        selected = sorted({int(item) for item in raw})
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid measured boot PCR selection") from exc
    if not selected or selected[0] < 0 or selected[-1] > 23:
        raise RuntimeError("measured boot PCR selection must be between 0 and 23")
    return selected



def _fallback_pcrs(policy: TrustPolicy) -> list[int]:
    raw = policy.content.get("fallback_pcrs") or [7]
    try:
        selected = sorted({int(item) for item in raw})
    except (TypeError, ValueError) as exc:
        raise RuntimeError("invalid fallback PCR selection for trusted boot policy") from exc
    if not selected or selected[0] < 0 or selected[-1] > 23:
        raise RuntimeError("fallback PCR selection must be between 0 and 23")
    return selected



def _parse_tpm2_pcrread_sha256(output: str, selected_pcrs: list[int]) -> dict[int, str]:
    values: dict[int, str] = {}
    for line in output.splitlines():
        match = re.match(r"\s*([0-9]+)\s*:\s*0x([0-9A-Fa-f]{64})\s*$", line)
        if match:
            values[int(match.group(1))] = match.group(2).lower()
    missing = [item for item in selected_pcrs if item not in values]
    if missing:
        raise RuntimeError(f"tpm2_pcrread output is missing sha256 PCR values: {missing}")
    return {item: values[item] for item in selected_pcrs}



def _tpm_policy_from_pcrs(pcr_values: dict[int, str]) -> dict[str, Any]:
    mask = 0
    policy: dict[str, Any] = {}
    for pcr, digest in sorted(pcr_values.items()):
        mask |= 1 << pcr
        policy[str(pcr)] = [digest.lower()]
    policy["mask"] = hex(mask)
    return policy
