"""Background task API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.api.deps import db_session, require_admin, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.models import TaskRun
from keylime_openstack.schemas import TaskRunOut
from keylime_openstack.services.sync import TrustSyncService
from keylime_openstack.services.tasks import create_task, mark_failed, mark_running, mark_success

router = APIRouter()


@router.get("/tasks", response_model=list[TaskRunOut])
def tasks(session: Session = Depends(db_session), limit: int = 50) -> list[TaskRunOut]:
    rows = session.scalars(select(TaskRun).order_by(TaskRun.created_at.desc()).limit(limit)).all()
    return [TaskRunOut.model_validate(item) for item in rows]


@router.post("/tasks/sync", dependencies=[Depends(require_admin)])
def run_sync_now(
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    task = create_task(session, "sync", requested_by="api")
    mark_running(task)
    try:
        result = TrustSyncService(session, settings).run_once()
    except Exception as exc:
        mark_failed(task, str(exc))
        session.commit()
        raise
    mark_success(task, result)
    session.commit()
    return {"ok": True, "task_id": task.id, "result": result}
