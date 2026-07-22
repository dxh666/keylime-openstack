"""System and metadata API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from keylime_openstack.api.deps import (
    db_session,
    require_admin,
    require_user_or_admin_token,
    settings_dep,
)
from keylime_openstack.config import Settings
from keylime_openstack.constants import DEFAULT_TRUST_TRAITS
from keylime_openstack.seed import ensure_default_environment

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
