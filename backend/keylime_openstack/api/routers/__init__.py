"""Domain routers for the management API."""

from __future__ import annotations

from keylime_openstack.api.routers.audit import router as audit_router
from keylime_openstack.api.routers.auth import router as auth_router
from keylime_openstack.api.routers.dashboard import router as dashboard_router
from keylime_openstack.api.routers.evidence import router as evidence_router
from keylime_openstack.api.routers.keylime import router as keylime_router
from keylime_openstack.api.routers.nodes import router as nodes_router
from keylime_openstack.api.routers.policies import router as policies_router
from keylime_openstack.api.routers.system import router as system_router
from keylime_openstack.api.routers.tasks import router as tasks_router
from keylime_openstack.api.routers.trust import router as trust_router

PUBLIC_ROUTERS = (
    system_router,
    auth_router,
)

AUTHENTICATED_ROUTERS = (
    dashboard_router,
    nodes_router,
    trust_router,
    keylime_router,
    policies_router,
    tasks_router,
    evidence_router,
    audit_router,
)

ROUTERS = PUBLIC_ROUTERS + AUTHENTICATED_ROUTERS
