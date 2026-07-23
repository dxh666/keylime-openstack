"""Session authentication API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from keylime_openstack.api.deps import db_session, optional_current_user, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.schemas import AuthStatusOut, LoginIn, UserOut
from keylime_openstack.services.audit import record_audit_event, set_audit_actor
from keylime_openstack.services.auth import (
    AuthenticatedUser,
    authenticate_admin,
    create_auth_session,
    load_auth_session,
    revoke_auth_session,
)

router = APIRouter()


@router.post("/auth/login", response_model=AuthStatusOut)
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> AuthStatusOut:
    username = payload.username.strip()
    password = payload.password
    if not authenticate_admin(settings, username, password):
        record_audit_event(
            session,
            event_type="auth_login",
            target=username,
            severity="warning",
        message="login failed",
        actor=username or "anonymous",
        event_details={
            "log_type": "system_operation",
            "subject_name": username or "anonymous",
            "object_name": "管理系统",
            "operation": "登录",
            "result": "失败",
        },
        )
        session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    token, auth_session = create_auth_session(session, settings, username, request)
    set_audit_actor(username)
    record_audit_event(
        session,
        event_type="auth_login",
        target=username,
        severity="info",
        message="login succeeded",
        event_details={
            "log_type": "system_operation",
            "subject_name": username,
            "object_name": "管理系统",
            "operation": "登录",
            "result": "成功",
            "session_id": auth_session.id,
        },
    )
    session.commit()
    response.set_cookie(
        settings.auth_cookie_name,
        token,
        max_age=max(int(settings.auth_session_ttl_seconds or 0), 300),
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return AuthStatusOut(authenticated=True, user=_user_out(username))


@router.get("/auth/me", response_model=AuthStatusOut)
def me(
    current_user: AuthenticatedUser | None = Depends(optional_current_user),
) -> AuthStatusOut:
    if not current_user:
        return AuthStatusOut(authenticated=False, user=None)
    return AuthStatusOut(
        authenticated=True,
        user=UserOut(
            username=current_user.username,
            display_name=current_user.display_name,
            role=current_user.role,
        ),
    )


@router.post("/auth/logout")
def logout(
    request: Request,
    response: Response,
    current_user: AuthenticatedUser | None = Depends(optional_current_user),
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    token = request.cookies.get(settings.auth_cookie_name, "")
    auth_session = load_auth_session(session, token)
    if auth_session:
        revoke_auth_session(auth_session)
    username = current_user.username if current_user else auth_session.username if auth_session else "anonymous"
    record_audit_event(
        session,
        event_type="auth_logout",
        target=username,
        severity="info",
        message="logout succeeded",
        actor=username,
        event_details={
            "log_type": "system_operation",
            "subject_name": username,
            "object_name": "管理系统",
            "operation": "注销",
            "result": "成功",
        },
    )
    session.commit()
    response.delete_cookie(settings.auth_cookie_name, path="/")
    return {"ok": True}


def _user_out(username: str) -> UserOut:
    return UserOut(username=username, display_name=username, role="admin")
