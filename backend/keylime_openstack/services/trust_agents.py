"""Node trust-agent helpers.

The trust plane can manage nodes backed by different local trust agents. Intel
nodes currently use Keylime agent, while the Hygon node can report evidence via
OpenTCSM with Hygon TPCM as the local trust root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from keylime_openstack.constants import TRUST_AGENT_KEYLIME, TRUST_AGENT_OPENTCSM_TPCM

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
        "": TRUST_AGENT_KEYLIME,
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
    return normalize_trust_agent_type(value)


def node_trust_agent_name(node: "ComputeNode", settings: "Settings") -> str:
    agent_type = node_trust_agent_type(node, settings)
    facts = node.facts or {}
    if facts.get("trust_agent_name"):
        return str(facts["trust_agent_name"])
    if agent_type == TRUST_AGENT_OPENTCSM_TPCM:
        return "OpenTCSM"
    return "Keylime Agent"


def node_trusted_root(node: "ComputeNode", settings: "Settings") -> str:
    facts = node.facts or {}
    if facts.get("trusted_root"):
        return str(facts["trusted_root"])
    if node_trust_agent_type(node, settings) == TRUST_AGENT_OPENTCSM_TPCM:
        return "Hygon TPCM"
    return "TPM 2.0"
