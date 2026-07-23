from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from keylime_openstack.api.routers.queries import _latest_openstack_states
from keylime_openstack.database import Base
from keylime_openstack.models import ComputeNode, OpenStackState
from keylime_openstack.services.sync_collectors import OpenStackStateCollector


class FakeOpenStack:
    def __init__(
        self,
        services: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.services = services or []
        self.error = error

    def list_compute_services(self) -> list[dict[str, Any]]:
        if self.error:
            raise self.error
        return self.services


def test_missing_compute_service_writes_down_state_over_stale_online_state() -> None:
    with _memory_session() as session:
        node = ComputeNode(
            hostname="csri8",
            hypervisor_name="csri8",
            management_ip="172.31.100.8",
            role="compute",
        )
        session.add(node)
        session.flush()
        session.add(
            OpenStackState(
                node_id=node.id,
                service_binary="nova-compute",
                service_status="enabled",
                service_state="up",
                raw={"source": "old"},
            )
        )
        session.flush()

        OpenStackStateCollector(session, FakeOpenStack([])).refresh([node])
        session.flush()

        latest = _latest_openstack_states(session, [node.id])[node.id]

    assert latest.service_status == "missing"
    assert latest.service_state == "down"
    assert latest.raw["reason"] == "nova-compute service not found in current refresh"


def test_openstack_collection_error_writes_unknown_state() -> None:
    with _memory_session() as session:
        node = ComputeNode(
            hostname="csri9",
            hypervisor_name="csri9",
            management_ip="172.31.100.9",
            role="compute",
        )
        session.add(node)
        session.flush()

        OpenStackStateCollector(
            session,
            FakeOpenStack(error=RuntimeError("openstack auth failed")),
        ).refresh([node])
        session.flush()

        latest = _latest_openstack_states(session, [node.id])[node.id]

    assert latest.service_status == "unknown"
    assert latest.service_state == "unknown"
    assert latest.raw["error"] == "openstack auth failed"


def test_compute_service_host_match_accepts_fqdn_service_host() -> None:
    with _memory_session() as session:
        node = ComputeNode(
            hostname="hygon23",
            hypervisor_name="hygon23",
            management_ip="172.31.100.23",
            role="compute",
        )
        session.add(node)
        session.flush()

        OpenStackStateCollector(
            session,
            FakeOpenStack(
                [
                    {
                        "Host": "hygon23.example.test",
                        "Binary": "nova-compute",
                        "Status": "enabled",
                        "State": "down",
                    }
                ]
            ),
        ).refresh([node])
        session.flush()

        latest = _latest_openstack_states(session, [node.id])[node.id]

    assert latest.service_status == "enabled"
    assert latest.service_state == "down"
    assert latest.raw["Host"] == "hygon23.example.test"


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine, future=True)
