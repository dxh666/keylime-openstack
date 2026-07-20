"""Management API routes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from fastapi import APIRouter, Depends, HTTPException, Request

from keylime_openstack.api.deps import db_session, require_admin, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.constants import DEFAULT_TRUST_TRAITS
from keylime_openstack.models import (
    AuditEvent,
    ComputeNode,
    HardwareProfile,
    OpenStackState,
    PolicyBinding,
    TaskRun,
    TrustDecision,
    TrustPolicy,
)
from keylime_openstack.schemas import (
    AuditEventOut,
    ComputeNodeOut,
    DashboardOut,
    HostIntegrityReportIn,
    OverviewOut,
    TaskRunOut,
    TrustDecisionOut,
    TrustPolicyIn,
    TrustPolicyOut,
)
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.host_integrity import host_integrity_report_to_evidence
from keylime_openstack.services.keylime_gate import keylime_only_check
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.policy import (
    bind_policy_to_nodes,
    canonical_policy_type,
    list_policies,
    load_policy,
    policy_out,
    validated_policy_payload,
)
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


@router.get("/nodes", response_model=list[ComputeNodeOut])
def nodes(session: Session = Depends(db_session)) -> list[ComputeNodeOut]:
    ensure_default_environment(session)
    session.commit()
    rows = session.scalars(
        select(ComputeNode).options(joinedload(ComputeNode.hardware_profile)).order_by(ComputeNode.hostname)
    ).all()
    states = _latest_openstack_states(session, [item.id for item in rows])
    return [
        ComputeNodeOut.model_validate(
            {
                **item.__dict__,
                "hardware_profile": item.hardware_profile,
                "openstack_state": states.get(item.id),
            }
        )
        for item in rows
    ]


@router.get("/keylime/check")
def keylime_check(
    hosts: str = "",
    failures_only: bool = False,
) -> dict[str, object]:
    return keylime_only_check(hosts=hosts, failures_only=failures_only)


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
    return [policy_out(session, item) for item in list_policies(session)]


@router.get("/policies/{policy_id}", response_model=TrustPolicyOut)
def get_policy(policy_id: int, session: Session = Depends(db_session)) -> TrustPolicyOut:
    policy = load_policy(session, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"策略不存在：{policy_id}")
    return policy_out(session, policy)


@router.post("/policies", response_model=TrustPolicyOut, dependencies=[Depends(require_admin)])
def create_policy(
    policy_in: TrustPolicyIn,
    session: Session = Depends(db_session),
) -> TrustPolicyOut:
    payload, node_ids, deploy_now = validated_policy_payload(policy_in)
    existing = session.scalars(
        select(TrustPolicy).where(TrustPolicy.name == payload["name"])
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"策略名称已存在：{payload['name']}")
    policy = TrustPolicy(**payload)
    session.add(policy)
    session.flush()
    bind_policy_to_nodes(session, policy, node_ids, deploy_now=deploy_now)
    if deploy_now:
        create_task(
            session,
            "policy_deploy",
            target=policy.name,
            requested_by="api",
            task_args={"policy_id": policy.id},
        )
    _record_policy_audit(session, "policy_create", policy, "created trust policy")
    session.commit()
    policy = load_policy(session, policy.id)
    assert policy is not None
    return policy_out(session, policy)


@router.put(
    "/policies/{policy_id}",
    response_model=TrustPolicyOut,
    dependencies=[Depends(require_admin)],
)
def update_policy(
    policy_id: int,
    policy_in: TrustPolicyIn,
    session: Session = Depends(db_session),
) -> TrustPolicyOut:
    policy = load_policy(session, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"策略不存在：{policy_id}")
    if any(
        binding.active and binding.application_status == "applied"
        for binding in policy.bindings
    ):
        raise HTTPException(
            status_code=409,
            detail="已下发策略不能原地修改，请新增一个策略版本进行替换",
        )
    payload, node_ids, deploy_now = validated_policy_payload(policy_in)
    duplicate = session.scalars(
        select(TrustPolicy).where(TrustPolicy.name == payload["name"], TrustPolicy.id != policy_id)
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"策略名称已存在：{payload['name']}")
    for key, value in payload.items():
        setattr(policy, key, value)
    for binding in list(policy.bindings):
        session.delete(binding)
    session.flush()
    bind_policy_to_nodes(session, policy, node_ids, deploy_now=deploy_now)
    if deploy_now:
        create_task(
            session,
            "policy_deploy",
            target=policy.name,
            requested_by="api",
            task_args={"policy_id": policy.id},
        )
    session.flush()
    _record_policy_audit(session, "policy_update", policy, "updated trust policy")
    session.commit()
    policy = load_policy(session, policy.id)
    assert policy is not None
    return policy_out(session, policy)


@router.post("/policies/{policy_id}/deploy", dependencies=[Depends(require_admin)])
def deploy_policy(policy_id: int, session: Session = Depends(db_session)) -> dict[str, object]:
    policy = load_policy(session, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"策略不存在：{policy_id}")
    if not policy.bindings:
        raise HTTPException(status_code=409, detail="策略尚未绑定节点")
    for binding in policy.bindings:
        binding.application_status = "queued"
        binding.last_error = ""
    task = create_task(
        session,
        "policy_deploy",
        target=policy.name,
        requested_by="api",
        task_args={"policy_id": policy.id},
    )
    _record_policy_audit(session, "policy_deploy_queued", policy, "queued policy deployment")
    session.commit()
    return {"ok": True, "policy_id": policy.id, "task_id": task.id}


@router.post(
    "/policies/{policy_id}/bindings/{binding_id}/deploy",
    dependencies=[Depends(require_admin)],
)
def deploy_policy_binding(
    policy_id: int,
    binding_id: int,
    session: Session = Depends(db_session),
) -> dict[str, object]:
    policy = load_policy(session, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"策略不存在：{policy_id}")
    binding = session.get(PolicyBinding, binding_id)
    if not binding or binding.policy_id != policy.id or not binding.active:
        raise HTTPException(status_code=404, detail=f"策略绑定不存在或已失效：{binding_id}")
    binding.application_status = "queued"
    binding.last_error = ""
    node = session.get(ComputeNode, binding.target_id)
    target = f"{policy.name}:{node.hostname if node else binding.target_id}"
    task = create_task(
        session,
        "policy_deploy",
        target=target,
        requested_by="api",
        task_args={"policy_id": policy.id, "binding_id": binding.id},
    )
    _record_policy_audit(session, "policy_deploy_queued", policy, "queued node policy deployment")
    session.commit()
    return {
        "ok": True,
        "policy_id": policy.id,
        "binding_id": binding.id,
        "task_id": task.id,
    }


@router.delete("/policies/{policy_id}", dependencies=[Depends(require_admin)])
def delete_policy(
    policy_id: int,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    policy = load_policy(session, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"策略不存在：{policy_id}")
    applied = [
        item
        for item in policy.bindings
        if item.active and item.application_status == "applied"
    ]
    if applied:
        raise HTTPException(
            status_code=409,
            detail="已下发策略必须先从节点撤回，才能删除",
        )
    keylime = KeylimeClient(settings)
    policy_type = canonical_policy_type(policy.policy_type)
    for binding in policy.bindings:
        if not binding.external_policy_name:
            continue
        result = keylime.tenant_tool_delete_named_policy(
            policy_type=policy_type,
            name=binding.external_policy_name,
        )
        if result.get("rc") != 0:
            detail = result.get("stderr") or result.get("stdout") or "Keylime 删除失败"
            raise HTTPException(status_code=502, detail=str(detail))
    policy_name = policy.name
    policy_type = policy.policy_type
    for binding in list(policy.bindings):
        session.delete(binding)
    session.flush()
    session.delete(policy)
    session.add(
        AuditEvent(
            event_type="policy_delete",
            target=policy_name,
            severity="info",
            message="deleted trust policy",
            event_details={"policy_id": policy_id, "policy_type": policy_type},
        )
    )
    session.commit()
    return {"ok": True, "policy_id": policy_id, "deleted": policy_name}


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


@router.post("/nodes/{hostname}/host-integrity", dependencies=[Depends(require_admin)])
def ingest_host_integrity(
    hostname: str,
    report: HostIntegrityReportIn,
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> dict[str, object]:
    ensure_default_environment(session)
    node = session.scalars(
        select(ComputeNode).where(
            (ComputeNode.hostname == hostname) | (ComputeNode.hypervisor_name == hostname)
        )
    ).first()
    if not node:
        raise HTTPException(status_code=404, detail=f"unknown compute node {hostname}")

    report_data = report.model_dump(mode="json")
    if report.hostname and report.hostname != hostname:
        report_data["reported_hostname"] = report.hostname

    evidence = host_integrity_report_to_evidence(node, report_data, settings)
    session.add(evidence)
    session.flush()
    session.add(
        AuditEvent(
            event_type="host_integrity_evidence_collect",
            target=node.hostname,
            severity="info" if evidence.status == "pass" else "warning",
            message=evidence.summary,
            event_details={
                "evidence_id": evidence.id,
                "status": evidence.status,
                "provider": evidence.provider,
            },
        )
    )
    session.commit()
    return {
        "ok": True,
        "node": node.hostname,
        "evidence_id": evidence.id,
        "status": evidence.status,
        "summary": evidence.summary,
    }


@router.get("/audit", response_model=list[AuditEventOut])
def audit(session: Session = Depends(db_session), limit: int = 100) -> list[AuditEventOut]:
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [AuditEventOut.model_validate(item) for item in rows]


@router.get("/traits")
def traits() -> dict[str, list[str]]:
    return {"traits": DEFAULT_TRUST_TRAITS}


def _record_policy_audit(
    session: Session,
    event_type: str,
    policy: TrustPolicy,
    message: str,
) -> None:
    session.add(
        AuditEvent(
            event_type=event_type,
            target=policy.name,
            severity="info",
            message=message,
            event_details={
                "policy_id": policy.id,
                "policy_type": policy.policy_type,
                "status": policy.status,
            },
        )
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


def _latest_openstack_states(session: Session, node_ids: list[int]) -> dict[int, OpenStackState]:
    if not node_ids:
        return {}
    rows = session.scalars(
        select(OpenStackState)
        .where(OpenStackState.node_id.in_(node_ids))
        .order_by(OpenStackState.updated_at.desc())
    ).all()
    latest: dict[int, OpenStackState] = {}
    for row in rows:
        latest.setdefault(row.node_id, row)
    return latest


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
