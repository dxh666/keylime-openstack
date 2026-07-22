"""Default inventory seed for the current csri8/csri9/hygon22 lab."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.models import ComputeNode, HardwareProfile


def ensure_default_environment(session: Session) -> None:
    """Create hardware profiles and known nodes when the database is empty."""

    profile_by_name = {
        profile.name: profile
        for profile in session.scalars(select(HardwareProfile)).all()
    }

    def profile(name: str, vendor: str, model: str, kernel_family: str, notes: str) -> HardwareProfile:
        item = profile_by_name.get(name)
        if item:
            return item
        item = HardwareProfile(
            name=name,
            vendor=vendor,
            model=model,
            kernel_family=kernel_family,
            notes=notes,
        )
        session.add(item)
        session.flush()
        profile_by_name[name] = item
        return item

    intel = profile(
        "intel-xeon-4216",
        "Intel",
        "Intel(R) Xeon(R) Silver 4216 CPU @ 2.10GHz",
        "ubuntu-6.8.0-134",
        "csri8/csri9/csri10 homogeneous Intel lab nodes.",
    )
    hygon = profile(
        "hygon-c86-7380",
        "Hygon",
        "Hygon C86-3G 7380 32-core Processor",
        "anolis-6.6.102",
        "Hygon TPCM heterogeneous compute nodes.",
    )

    nodes = {node.hostname: node for node in session.scalars(select(ComputeNode)).all()}
    defaults = [
        {
            "hostname": "csri10",
            "hypervisor_name": "",
            "management_ip": "172.31.100.10",
            "role": "controller",
            "hardware_profile": intel,
            "facts": {
                "kernel": "6.8.0-134-generic",
                "cpu_vendor": "GenuineIntel",
                "cpu_count": 32,
            },
        },
        {
            "hostname": "csri8",
            "hypervisor_name": "csri8",
            "management_ip": "172.31.100.8",
            "role": "compute",
            "keylime_agent_ip": "172.31.100.8",
            "keylime_agent_uuid": "22222222-2222-4222-8222-000000000008",
            "hardware_profile": intel,
            "facts": {
                "kernel": "6.8.0-134-generic",
                "cpu_vendor": "GenuineIntel",
                "cpu_count": 32,
                "trust_agent_type": "keylime",
                "trust_agent_name": "Keylime Agent",
                "trusted_root": "TPM 2.0",
            },
        },
        {
            "hostname": "csri9",
            "hypervisor_name": "csri9",
            "management_ip": "172.31.100.9",
            "role": "compute",
            "keylime_agent_ip": "172.31.100.9",
            "keylime_agent_uuid": "11111111-1111-4111-8111-000000000009",
            "hardware_profile": intel,
            "facts": {
                "kernel": "6.8.0-134-generic",
                "cpu_vendor": "GenuineIntel",
                "cpu_count": 32,
                "trust_agent_type": "keylime",
                "trust_agent_name": "Keylime Agent",
                "trusted_root": "TPM 2.0",
            },
        },
        {
            "hostname": "hygon22",
            "hypervisor_name": "hygon22",
            "management_ip": "172.31.100.22",
            "role": "compute",
            "keylime_agent_ip": "",
            "keylime_agent_uuid": "",
            "hardware_profile": hygon,
            "facts": {
                "kernel": "6.6.102-5.3.3.an23.x86_64",
                "cpu_vendor": "HygonGenuine",
                "cpu_count": 128,
                "numa_nodes": 8,
                "trusted_root_type": "tpcm",
                "trusted_root": "Hygon TPCM",
            },
        },
        {
            "hostname": "hygon23",
            "hypervisor_name": "hygon23",
            "management_ip": "172.31.100.23",
            "role": "compute",
            "keylime_agent_ip": "",
            "keylime_agent_uuid": "",
            "hardware_profile": hygon,
            "facts": {
                "kernel": "6.6.102-5.3.2.an23.x86_64",
                "os": "Anolis OS 23.4",
                "trust_agent_type": "opentcsm_tpcm",
                "trust_agent_name": "OpenTCSM",
                "trusted_root": "Hygon TPCM",
                "tpcm_id": "E9FA9758029E6318B21093811A51D4EC",
            },
        },
    ]
    for payload in defaults:
        existing = nodes.get(payload["hostname"])
        if existing:
            _fill_missing_node_defaults(existing, payload)
            continue
        node = ComputeNode(**payload)
        session.add(node)


def _fill_missing_node_defaults(node: ComputeNode, payload: dict[str, object]) -> None:
    for field in (
        "hypervisor_name",
        "management_ip",
        "keylime_agent_ip",
        "keylime_agent_uuid",
    ):
        value = payload.get(field)
        current = getattr(node, field)
        if value and (not current or current == node.hostname):
            setattr(node, field, value)
    if node.hardware_profile_id is None and payload.get("hardware_profile"):
        node.hardware_profile = payload["hardware_profile"]  # type: ignore[assignment]
    if isinstance(payload.get("facts"), dict):
        merged = dict(node.facts or {})
        changed = False
        for key, value in payload["facts"].items():  # type: ignore[union-attr]
            if key not in merged or merged[key] in ("", None):
                merged[key] = value
                changed = True
        if changed or not node.facts:
            node.facts = merged
