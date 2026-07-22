"""FastAPI dependencies."""

from __future__ import annotations

import hmac
from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings, get_settings
from keylime_openstack.database import SessionLocal
from keylime_openstack.services.audit import clear_audit_actor, set_audit_actor
from keylime_openstack.services.auth import AuthenticatedUser, load_auth_session, session_user


def settings_dep() -> Settings:
    return get_settings()


def db_session() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def optional_current_user(
    request: Request,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> AuthenticatedUser | None:
    token = request.cookies.get(settings.auth_cookie_name, "")
    auth_session = load_auth_session(session, token)
    if not auth_session:
        clear_audit_actor()
        return None
    user = session_user(auth_session)
    set_audit_actor(user.username)
    return user


def require_user(
    current_user: AuthenticatedUser | None = Depends(optional_current_user),
) -> AuthenticatedUser:
    if not current_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    return current_user


def require_user_or_admin_token(
    settings: Settings = Depends(settings_dep),
    current_user: AuthenticatedUser | None = Depends(optional_current_user),
    x_admin_token: str = Header(default="", alias="X-Admin-Token"),
) -> AuthenticatedUser | None:
    if current_user:
        return current_user
    if not settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    if not hmac.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid admin token")
    set_audit_actor("admin-token")
    return None


def require_admin(
    _: AuthenticatedUser | None = Depends(require_user_or_admin_token),
) -> None:
    return None
