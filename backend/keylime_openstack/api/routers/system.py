"""System and metadata API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from keylime_openstack.api.deps import (
    db_session,
    require_admin,
    require_user_or_admin_token,
    settings_dep,
)
from keylime_openstack.config import Settings
from keylime_openstack.constants import DEFAULT_TRUST_TRAITS
from keylime_openstack.schemas import TpcmGlobalPolicyApplyIn
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.tpcm_global_policy import (
    get_tpcm_global_policy_state,
    queue_tpcm_global_policy_apply,
)

router = APIRouter()


@router.get("/health")
def health(settings: Settings = Depends(settings_dep)) -> dict[str, object]:
    return {
        "ok": True,
        "service": settings.service_name,
        "environment": settings.environment,
    }


@router.post("/bootstrap", dependencies=[Depends(require_admin)])
def bootstrap(session: Session = Depends(db_session)) -> dict[str, object]:
    ensure_default_environment(session)
    session.commit()
    return {"ok": True}


@router.get("/traits", dependencies=[Depends(require_user_or_admin_token)])
def traits() -> dict[str, list[str]]:
    return {"traits": DEFAULT_TRUST_TRAITS}


@router.get("/system/tpcm/global-policy", dependencies=[Depends(require_user_or_admin_token)])
def tpcm_global_policy_state(
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    ensure_default_environment(session)
    result = get_tpcm_global_policy_state(session, settings)
    session.commit()
    return result


@router.post("/system/tpcm/global-policy", dependencies=[Depends(require_admin)])
def apply_tpcm_global_policy(
    payload: TpcmGlobalPolicyApplyIn,
    session: Session = Depends(db_session),
) -> dict[str, object]:
    ensure_default_environment(session)
    try:
        result = queue_tpcm_global_policy_apply(session, payload.fields)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return result
