"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings, get_settings
from keylime_openstack.database import SessionLocal


def settings_dep() -> Settings:
    return get_settings()


def db_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def require_admin(
    settings: Settings = Depends(settings_dep),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
) -> None:
    if not settings.admin_token:
        return
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")
