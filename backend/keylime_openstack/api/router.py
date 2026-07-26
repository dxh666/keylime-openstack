"""Management API router aggregation."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from keylime_openstack.api.deps import require_user_or_admin_token
from keylime_openstack.api.routers import AUTHENTICATED_ROUTERS, PUBLIC_ROUTERS
from keylime_openstack.api.routers.audit import audit
from keylime_openstack.api.routers.auth import login, logout, me
from keylime_openstack.api.routers.dashboard import (
    _dashboard_control_node,
    _dashboard_control_plane,
    _dashboard_current_user,
    _infer_os,
    dashboard,
    overview,
)
from keylime_openstack.api.routers.evidence import (
    _access_check_item,
    _looks_like_ssh_error,
    _opentcsm_command_errors,
    _opentcsm_failed_access_checks,
    _truncate_detail,
    _yes_no,
    check_opentcsm_access,
    collect_opentcsm_evidence,
    ingest_host_integrity,
    ingest_opentcsm_evidence,
)
from keylime_openstack.api.routers.keylime import keylime_check
from keylime_openstack.api.routers.nodes import hardware_profiles, nodes
from keylime_openstack.api.routers.policies import (
    _dynamic_policy_audit_details,
    _record_policy_audit,
    _tpcm_dynamic_global_control,
    _tpcm_dynamic_global_enabled,
    create_policy,
    delete_policy,
    deploy_policy,
    deploy_policy_binding,
    get_policy,
    get_tpcm_dynamic_global_switch,
    policies,
    set_tpcm_dynamic_global_switch,
    update_policy,
)
from keylime_openstack.api.routers.queries import _latest_decisions, _latest_openstack_states
from keylime_openstack.api.routers.system import (
    apply_tpcm_global_policy,
    bootstrap,
    health,
    tpcm_global_policy_state,
    traits,
)
from keylime_openstack.api.routers.tasks import run_sync_now, tasks
from keylime_openstack.api.routers.trust import (
    register_trust_agent,
    sync_trust_registrations,
    trust_check,
    trust_registrations,
    verify_trust_agent,
)

router = APIRouter(prefix="/api")
for domain_router in PUBLIC_ROUTERS:
    router.include_router(domain_router)
for domain_router in AUTHENTICATED_ROUTERS:
    router.include_router(domain_router, dependencies=[Depends(require_user_or_admin_token)])

__all__ = [
    "router",
    "health",
    "bootstrap",
    "login",
    "me",
    "logout",
    "dashboard",
    "overview",
    "nodes",
    "trust_check",
    "trust_registrations",
    "sync_trust_registrations",
    "verify_trust_agent",
    "register_trust_agent",
    "keylime_check",
    "hardware_profiles",
    "policies",
    "set_tpcm_dynamic_global_switch",
    "get_tpcm_dynamic_global_switch",
    "get_policy",
    "create_policy",
    "update_policy",
    "deploy_policy",
    "deploy_policy_binding",
    "delete_policy",
    "tasks",
    "run_sync_now",
    "ingest_host_integrity",
    "ingest_opentcsm_evidence",
    "collect_opentcsm_evidence",
    "check_opentcsm_access",
    "audit",
    "traits",
    "tpcm_global_policy_state",
    "apply_tpcm_global_policy",
    "_access_check_item",
    "_opentcsm_failed_access_checks",
    "_opentcsm_command_errors",
    "_looks_like_ssh_error",
    "_yes_no",
    "_truncate_detail",
    "_record_policy_audit",
    "_tpcm_dynamic_global_enabled",
    "_tpcm_dynamic_global_control",
    "_dynamic_policy_audit_details",
    "_dashboard_control_node",
    "_infer_os",
    "_dashboard_control_plane",
    "_dashboard_current_user",
    "_latest_openstack_states",
    "_latest_decisions",
]
