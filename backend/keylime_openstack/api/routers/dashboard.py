"""Dashboard and overview API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from keylime_openstack.api.deps import db_session, settings_dep
from keylime_openstack.api.routers.queries import _latest_decisions
from keylime_openstack.config import Settings
from keylime_openstack.constants import DEFAULT_TRUST_TRAITS
from keylime_openstack.models import ComputeNode, TaskRun
from keylime_openstack.schemas import DashboardOut, OverviewOut, TaskRunOut, TrustDecisionOut
from keylime_openstack.seed import ensure_default_environment

router = APIRouter()


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    request: Request,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> DashboardOut:
    ensure_default_environment(session)
    session.commit()
    controller = session.scalars(
        select(ComputeNode)
        .options(joinedload(ComputeNode.hardware_profile))
        .where(ComputeNode.role == "controller")
        .order_by(ComputeNode.hostname)
        .limit(1)
    ).first()
    if not controller:
        raise HTTPException(status_code=404, detail="控制节点尚未初始化")

    return DashboardOut(
        control_node=_dashboard_control_node(controller),
        control_plane_status=_dashboard_control_plane(settings),
        online_users=[_dashboard_current_user(request)],
    )


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
        trust_policy_mode=settings.normalized_trust_policy_mode,
        trust_capabilities=settings.effective_trust_capabilities,
    )


def _dashboard_control_node(node: ComputeNode) -> dict[str, object]:
    facts = node.facts or {}
    profile = node.hardware_profile
    kernel = str(facts.get("kernel") or "")
    return {
        "hostname": node.hostname,
        "management_ip": node.management_ip,
        "operating_system": str(facts.get("os") or _infer_os(kernel)),
        "kernel": kernel or "-",
        "cpu_model": (
            profile.model
            if profile and profile.model
            else str(facts.get("cpu_model") or facts.get("cpu_vendor") or "-")
        ),
        "cpu_count": facts.get("cpu_count") or "-",
        "deployment_mode": "Kolla / Docker",
    }


def _infer_os(kernel: str) -> str:
    if "generic" in kernel:
        return "Ubuntu Server"
    if "an23" in kernel:
        return "Anolis OS 23"
    return "-"


def _dashboard_control_plane(settings: Settings) -> list[dict[str, str]]:
    keylime_configured = bool(settings.keylime_verifier_url and settings.keylime_registrar_url)
    openstack_configured = bool(settings.openstack_clouds_yaml or settings.openstack_openrc)
    return [
        {"name": "管理 API", "status": "正常", "state": "ok"},
        {"name": "PostgreSQL", "status": "正常", "state": "ok"},
        {
            "name": "Keylime Registrar",
            "status": "已配置" if keylime_configured else "待接入",
            "state": "ok" if keylime_configured else "warn",
        },
        {
            "name": "Keylime Verifier",
            "status": "已配置" if keylime_configured else "待接入",
            "state": "ok" if keylime_configured else "warn",
        },
        {
            "name": "OpenStack 控制面",
            "status": "已配置" if openstack_configured else "待接入",
            "state": "ok" if openstack_configured else "warn",
        },
    ]


def _dashboard_current_user(request: Request) -> dict[str, str]:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    forwarded_user = request.headers.get("x-forwarded-user", "")
    forwarded_group = request.headers.get("x-forwarded-groups", "")
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    client_host = request.client.host if request.client else ""
    return {
        "type": (forwarded_proto or request.url.scheme or "http").upper(),
        "username": forwarded_user or "admin",
        "user_group": forwarded_group or "Administrator",
        "ip_address": (forwarded_for.split(",", 1)[0].strip() if forwarded_for else client_host) or "-",
    }
