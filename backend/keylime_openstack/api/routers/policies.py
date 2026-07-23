"""Trust policy management API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from keylime_openstack.api.deps import db_session, require_admin, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.schemas import (
    TpcmDynamicGlobalSwitchIn,
    TrustPolicyIn,
    TrustPolicyOut,
)
from keylime_openstack.services.policy_management import (
    _dynamic_policy_audit_details,
    _record_policy_audit,
    _tpcm_dynamic_global_control,
    _tpcm_dynamic_global_enabled,
    create_policy as create_policy_service,
    delete_policy as delete_policy_service,
    deploy_policy as deploy_policy_service,
    deploy_policy_binding as deploy_policy_binding_service,
    get_policy_response,
    get_tpcm_dynamic_global_switch as get_tpcm_dynamic_global_switch_service,
    list_policy_responses,
    set_tpcm_dynamic_global_switch as set_tpcm_dynamic_global_switch_service,
    update_policy as update_policy_service,
)

router = APIRouter()


@router.get("/policies", response_model=list[TrustPolicyOut])
def policies(session: Session = Depends(db_session)) -> list[TrustPolicyOut]:
    return list_policy_responses(session)


@router.post("/policies/tpcm-dynamic/global-switch", dependencies=[Depends(require_admin)])
def set_tpcm_dynamic_global_switch(
    request: TpcmDynamicGlobalSwitchIn,
    session: Session = Depends(db_session),
) -> dict[str, object]:
    return set_tpcm_dynamic_global_switch_service(request, session)


@router.get("/policies/tpcm-dynamic/global-switch")
def get_tpcm_dynamic_global_switch(
    session: Session = Depends(db_session),
) -> dict[str, object]:
    return get_tpcm_dynamic_global_switch_service(session)


@router.get("/policies/{policy_id}", response_model=TrustPolicyOut)
def get_policy(policy_id: int, session: Session = Depends(db_session)) -> TrustPolicyOut:
    return get_policy_response(policy_id, session)


@router.post("/policies", response_model=TrustPolicyOut, dependencies=[Depends(require_admin)])
def create_policy(
    policy_in: TrustPolicyIn,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> TrustPolicyOut:
    return create_policy_service(policy_in, session, settings)


@router.put(
    "/policies/{policy_id}",
    response_model=TrustPolicyOut,
    dependencies=[Depends(require_admin)],
)
def update_policy(
    policy_id: int,
    policy_in: TrustPolicyIn,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> TrustPolicyOut:
    return update_policy_service(policy_id, policy_in, session, settings)


@router.post("/policies/{policy_id}/deploy", dependencies=[Depends(require_admin)])
def deploy_policy(policy_id: int, session: Session = Depends(db_session)) -> dict[str, object]:
    return deploy_policy_service(policy_id, session)


@router.post(
    "/policies/{policy_id}/bindings/{binding_id}/deploy",
    dependencies=[Depends(require_admin)],
)
def deploy_policy_binding(
    policy_id: int,
    binding_id: int,
    session: Session = Depends(db_session),
) -> dict[str, object]:
    return deploy_policy_binding_service(policy_id, binding_id, session)


@router.delete("/policies/{policy_id}", dependencies=[Depends(require_admin)])
def delete_policy(
    policy_id: int,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    return delete_policy_service(policy_id, session, settings)
