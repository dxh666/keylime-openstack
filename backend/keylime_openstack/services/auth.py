"""Local session authentication helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.models import AuthSession

__all__ = [
    "AuthenticatedUser",
    "authenticate_admin",
    "create_auth_session",
    "hash_session_token",
    "load_auth_session",
    "new_session_token",
    "revoke_auth_session",
    "session_user",
]


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    display_name: str
    role: str = "admin"
    session_id: int | None = None


def authenticate_admin(settings: Settings, username: str, password: str) -> bool:
    expected_username = settings.admin_username or "admin"
    expected_password = settings.admin_password or settings.admin_token
    if not expected_password and settings.environment.lower() != "production":
        expected_password = "admin"
    if not expected_password:
        return False
    return hmac.compare_digest(username, expected_username) and hmac.compare_digest(
        password,
        expected_password,
    )


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_auth_session(
    session: Session,
    settings: Settings,
    username: str,
    request: Request,
) -> tuple[str, AuthSession]:
    token = new_session_token()
    ttl_seconds = max(int(settings.auth_session_ttl_seconds or 0), 300)
    item = AuthSession(
        session_token_hash=hash_session_token(token),
        username=username,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        client_ip=request.client.host if request.client else "",
        user_agent=request.headers.get("user-agent", ""),
    )
    session.add(item)
    session.flush()
    return token, item


def load_auth_session(session: Session, token: str) -> AuthSession | None:
    if not token:
        return None
    item = session.scalars(
        select(AuthSession).where(AuthSession.session_token_hash == hash_session_token(token))
    ).first()
    if not item or item.revoked_at is not None:
        return None
    expires_at = _aware_utc(item.expires_at)
    if expires_at <= datetime.now(timezone.utc):
        return None
    return item


def revoke_auth_session(item: AuthSession) -> None:
    item.revoked_at = datetime.now(timezone.utc)


def session_user(item: AuthSession) -> AuthenticatedUser:
    return AuthenticatedUser(
        username=item.username,
        display_name=item.username,
        role="admin",
        session_id=item.id,
    )


def _aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
