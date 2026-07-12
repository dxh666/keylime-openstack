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
        "hygon22 heterogeneous compute node.",
    )

    nodes = {node.hostname: node for node in session.scalars(select(ComputeNode)).all()}
    defaults = [
        {
            "hostname": "csri10",
            "hypervisor_name": "",
            "management_ip": "172.31.100.10",
            "role": "controller",
            "hardware_profile": intel,
            "facts": {"kernel": "6.8.0-134-generic", "cpu_vendor": "GenuineIntel", "cpu_count": 32},
        },
        {
            "hostname": "csri8",
            "hypervisor_name": "csri8",
            "management_ip": "172.31.100.8",
            "role": "compute",
            "keylime_agent_ip": "172.31.100.8",
            "keylime_agent_uuid": "22222222-2222-4222-8222-000000000008",
            "hardware_profile": intel,
            "facts": {"kernel": "6.8.0-134-generic", "cpu_vendor": "GenuineIntel", "cpu_count": 32},
        },
        {
            "hostname": "csri9",
            "hypervisor_name": "csri9",
            "management_ip": "172.31.100.9",
            "role": "compute",
            "keylime_agent_ip": "172.31.100.9",
            "keylime_agent_uuid": "11111111-1111-4111-8111-000000000009",
            "hardware_profile": intel,
            "facts": {"kernel": "6.8.0-134-generic", "cpu_vendor": "GenuineIntel", "cpu_count": 32},
        },
        {
            "hostname": "hygon22",
            "hypervisor_name": "hygon22",
            "management_ip": "hygon22",
            "role": "compute",
            "keylime_agent_ip": "hygon22",
            "hardware_profile": hygon,
            "facts": {
                "kernel": "6.6.102-5.3.3.an23.x86_64",
                "cpu_vendor": "HygonGenuine",
                "cpu_count": 128,
                "numa_nodes": 8,
            },
        },
    ]
    for payload in defaults:
        if payload["hostname"] in nodes:
            continue
        node = ComputeNode(**payload)
        session.add(node)
