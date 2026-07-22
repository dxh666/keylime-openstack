"""Shared audit event recording helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.models import AuditEvent

__all__ = ["record_audit_event"]


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

    event = AuditEvent(
        event_type=event_type,
        actor=actor,
        target=target,
        severity=severity,
        message=message,
        event_details=dict(event_details or {}),
    )
    session.add(event)
    return event
