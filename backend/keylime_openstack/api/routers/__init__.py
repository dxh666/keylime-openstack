"""Domain routers for the management API."""

from __future__ import annotations

from keylime_openstack.api.routers.audit import router as audit_router
from keylime_openstack.api.routers.dashboard import router as dashboard_router
from keylime_openstack.api.routers.evidence import router as evidence_router
from keylime_openstack.api.routers.keylime import router as keylime_router
from keylime_openstack.api.routers.nodes import router as nodes_router
from keylime_openstack.api.routers.policies import router as policies_router
from keylime_openstack.api.routers.system import router as system_router
from keylime_openstack.api.routers.tasks import router as tasks_router

ROUTERS = (
    system_router,
    dashboard_router,
    nodes_router,
    keylime_router,
    policies_router,
    tasks_router,
    evidence_router,
    audit_router,
)
