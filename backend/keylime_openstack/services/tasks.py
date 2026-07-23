"""Task state helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.constants import TASK_FAILED, TASK_PENDING, TASK_RUNNING, TASK_SUCCESS
from keylime_openstack.models import TaskRun
from keylime_openstack.services.audit import current_audit_actor


def create_task(
    session: Session,
    task_type: str,
    *,
    target: str = "",
    requested_by: str = "system",
    task_args: dict[str, Any] | None = None,
) -> TaskRun:
    if requested_by == "api":
        requested_by = current_audit_actor("api")
    task = TaskRun(
        task_type=task_type,
        status=TASK_PENDING,
        target=target,
        requested_by=requested_by,
        task_args=_json_safe(task_args or {}),
    )
    session.add(task)
    session.flush()
    return task


def mark_running(task: TaskRun) -> None:
    task.status = TASK_RUNNING
    task.started_at = datetime.now(timezone.utc)


def mark_success(task: TaskRun, result: dict[str, Any] | None = None) -> None:
    task.status = TASK_SUCCESS
    task.finished_at = datetime.now(timezone.utc)
    task.result = _json_safe(result or {})


def mark_failed(task: TaskRun, error: str, result: dict[str, Any] | None = None) -> None:
    task.status = TASK_FAILED
    task.finished_at = datetime.now(timezone.utc)
    task.error = error
    task.result = _json_safe(result or {})


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
