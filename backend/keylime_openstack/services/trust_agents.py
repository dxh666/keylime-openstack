"""Node trusted-root and management-state helpers.

Product state is expressed as compute/non-compute, managed/unmanaged, and
trusted root type. Keylime and OpenTCSM remain implementation adapters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from keylime_openstack.constants import (
    TRUST_AGENT_KEYLIME,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_AGENT_UNMANAGED,
    TRUST_ROOT_TPCM,
    TRUST_ROOT_TPM,
    TRUST_ROOT_UNKNOWN,
)

if TYPE_CHECKING:  # pragma: no cover
    from keylime_openstack.config import Settings
    from keylime_openstack.models import ComputeNode


def parse_host_map(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in (raw or "").split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and value:
            mapping[key] = value
    return mapping


def normalize_trust_agent_type(value: str) -> str:
    normalized = (value or "").strip().lower().replace("-", "_")
    aliases = {
        "": TRUST_AGENT_UNMANAGED,
        "none": TRUST_AGENT_UNMANAGED,
        "unmanaged": TRUST_AGENT_UNMANAGED,
        "not_managed": TRUST_AGENT_UNMANAGED,
        "disabled": TRUST_AGENT_UNMANAGED,
        "keylime": TRUST_AGENT_KEYLIME,
        "keylime_agent": TRUST_AGENT_KEYLIME,
        "opentcsm": TRUST_AGENT_OPENTCSM_TPCM,
        "opentcsm_tpcm": TRUST_AGENT_OPENTCSM_TPCM,
        "hygon_tpcm": TRUST_AGENT_OPENTCSM_TPCM,
        "tpcm": TRUST_AGENT_OPENTCSM_TPCM,
    }
    return aliases.get(normalized, normalized)


def node_trust_agent_type(node: "ComputeNode", settings: "Settings") -> str:
    mapping = parse_host_map(settings.trust_agent_type_map)
    mapped = mapping.get(node.hostname) or mapping.get(node.hypervisor_name)
    facts = node.facts or {}
    value = mapped or str(facts.get("trust_agent_type") or "")
    if not value and node.keylime_agent_uuid:
        return TRUST_AGENT_KEYLIME
    return normalize_trust_agent_type(value)


def node_trust_managed(node: "ComputeNode", settings: "Settings") -> bool:
    return node_trust_agent_type(node, settings) != TRUST_AGENT_UNMANAGED


def node_trust_agent_name(node: "ComputeNode", settings: "Settings") -> str:
    agent_type = node_trust_agent_type(node, settings)
    facts = node.facts or {}
    if facts.get("trust_agent_name"):
        return str(facts["trust_agent_name"])
    if agent_type == TRUST_AGENT_UNMANAGED:
        return "Unmanaged"
    if agent_type == TRUST_AGENT_OPENTCSM_TPCM:
        return "OpenTCSM"
    return "Keylime Agent"


def node_trusted_root_type(node: "ComputeNode", settings: "Settings") -> str:
    facts = node.facts or {}
    value = str(facts.get("trusted_root_type") or facts.get("trusted_root") or "").lower()
    agent_type = node_trust_agent_type(node, settings)
    if "tpcm" in value or agent_type == TRUST_AGENT_OPENTCSM_TPCM:
        return TRUST_ROOT_TPCM
    if "tpm" in value or agent_type == TRUST_AGENT_KEYLIME:
        return TRUST_ROOT_TPM
    return TRUST_ROOT_UNKNOWN


def node_trusted_root(node: "ComputeNode", settings: "Settings") -> str:
    facts = node.facts or {}
    if facts.get("trusted_root"):
        return str(facts["trusted_root"])
    trusted_root_type = node_trusted_root_type(node, settings)
    if trusted_root_type == TRUST_ROOT_TPCM:
        return "Hygon TPCM"
    if trusted_root_type == TRUST_ROOT_TPM:
        return "TPM 2.0"
    return TRUST_ROOT_UNKNOWN
