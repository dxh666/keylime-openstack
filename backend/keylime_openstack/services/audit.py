"""Shared audit event recording helpers."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.models import AuditEvent

_AUDIT_ACTOR: ContextVar[str] = ContextVar("audit_actor", default="")

__all__ = ["clear_audit_actor", "current_audit_actor", "record_audit_event", "set_audit_actor"]


def set_audit_actor(actor: str) -> None:
    _AUDIT_ACTOR.set(actor.strip())


def clear_audit_actor() -> None:
    _AUDIT_ACTOR.set("")


def current_audit_actor(default: str = "system") -> str:
    return _AUDIT_ACTOR.get() or default


def record_audit_event(
    session: Session,
    *,
    event_type: str,
    target: str = "",
    severity: str = "info",
    message: str = "",
    actor: str = "system",
    event_details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Create and stage an audit event without changing the caller's transaction."""

    if actor == "system":
        actor = _AUDIT_ACTOR.get() or actor
    event = AuditEvent(
        event_type=event_type,
        actor=actor,
        target=target,
        severity=severity,
        message=message,
        event_details=_json_safe(event_details or {}),
    )
    session.add(event)
    return event


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
