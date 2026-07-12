"""Management API routes."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from fastapi import APIRouter, Depends

from keylime_openstack.api.deps import db_session, require_admin, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.constants import DEFAULT_TRUST_TRAITS
from keylime_openstack.models import (
    AuditEvent,
    ComputeNode,
    HardwareProfile,
    TaskRun,
    TrustDecision,
    TrustPolicy,
)
from keylime_openstack.schemas import (
    AuditEventOut,
    ComputeNodeOut,
    OverviewOut,
    TaskRunOut,
    TrustDecisionOut,
    TrustPolicyOut,
)
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.sync import TrustSyncService
from keylime_openstack.services.tasks import create_task, mark_failed, mark_running, mark_success

router = APIRouter(prefix="/api")


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


@router.get("/overview", response_model=OverviewOut)
def overview(
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> OverviewOut:
    ensure_default_environment(session)
    session.commit()
    compute_node_ids = [
        item[0]
        for item in session.execute(
            select(ComputeNode.id)
            .where(ComputeNode.role == "compute")
            .where(ComputeNode.enabled.is_(True))
            .order_by(ComputeNode.hostname)
        ).all()
    ]
    nodes_total = len(compute_node_ids)
    latest_decisions = _latest_decisions(session, limit=20, node_ids=compute_node_ids)
    trusted_ids = {item.node_id for item in latest_decisions if item.trusted}
    boot_ids = {item.node_id for item in latest_decisions if item.boot_trusted}
    runtime_ids = {item.node_id for item in latest_decisions if item.runtime_trusted}
    worker_task = session.scalars(select(TaskRun).order_by(TaskRun.created_at.desc()).limit(1)).first()
    return OverviewOut(
        nodes_total=nodes_total,
        nodes_trusted=len(trusted_ids),
        nodes_boot_trusted=len(boot_ids),
        nodes_runtime_trusted=len(runtime_ids),
        nodes_untrusted=max(nodes_total - len(trusted_ids), 0),
        latest_decisions=[TrustDecisionOut.model_validate(item) for item in latest_decisions],
        worker={
            "last_task": TaskRunOut.model_validate(worker_task).model_dump() if worker_task else None,
            "interval_seconds": settings.worker_interval_seconds,
            "openstack_enforcement_enabled": settings.openstack_enforcement_enabled,
        },
        traits=DEFAULT_TRUST_TRAITS,
    )


@router.get("/nodes", response_model=list[ComputeNodeOut])
def nodes(session: Session = Depends(db_session)) -> list[ComputeNodeOut]:
    ensure_default_environment(session)
    session.commit()
    rows = session.scalars(
        select(ComputeNode).options(joinedload(ComputeNode.hardware_profile)).order_by(ComputeNode.hostname)
    ).all()
    return [ComputeNodeOut.model_validate(item) for item in rows]


@router.get("/hardware-profiles")
def hardware_profiles(session: Session = Depends(db_session)) -> list[dict[str, object]]:
    ensure_default_environment(session)
    session.commit()
    rows = session.scalars(select(HardwareProfile).order_by(HardwareProfile.name)).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "vendor": item.vendor,
            "model": item.model,
            "kernel_family": item.kernel_family,
        }
        for item in rows
    ]


@router.get("/policies", response_model=list[TrustPolicyOut])
def policies(session: Session = Depends(db_session)) -> list[TrustPolicyOut]:
    rows = session.scalars(select(TrustPolicy).order_by(TrustPolicy.policy_type, TrustPolicy.name)).all()
    return [TrustPolicyOut.model_validate(item) for item in rows]


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


@router.get("/audit", response_model=list[AuditEventOut])
def audit(session: Session = Depends(db_session), limit: int = 100) -> list[AuditEventOut]:
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [AuditEventOut.model_validate(item) for item in rows]


@router.get("/traits")
def traits() -> dict[str, list[str]]:
    return {"traits": DEFAULT_TRUST_TRAITS}


def _latest_decisions(session: Session, limit: int, node_ids: list[int] | None = None) -> list[TrustDecision]:
    if node_ids == []:
        return []
    statement = select(TrustDecision).order_by(TrustDecision.decided_at.desc()).limit(limit)
    if node_ids is not None:
        statement = statement.where(TrustDecision.node_id.in_(node_ids))
    rows = session.scalars(statement).all()
    latest: dict[int, TrustDecision] = {}
    for row in rows:
        latest.setdefault(row.node_id, row)
    return list(latest.values())
