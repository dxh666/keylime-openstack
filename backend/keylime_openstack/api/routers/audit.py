"""Audit log API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.api.deps import db_session
from keylime_openstack.models import AuditEvent
from keylime_openstack.schemas import AuditEventOut

router = APIRouter()


@router.get("/audit", response_model=list[AuditEventOut])
def audit(session: Session = Depends(db_session), limit: int = 100) -> list[AuditEventOut]:
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [AuditEventOut.model_validate(item) for item in rows]
