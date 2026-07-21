"""Management API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from fastapi import APIRouter, Depends, HTTPException, Request

from keylime_openstack.api.deps import db_session, require_admin, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    DEFAULT_TRUST_TRAITS,
    POLICY_TPCM_DYNAMIC_MEASUREMENT,
    TRUST_AGENT_OPENTCSM_TPCM,
)
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
    OpenTcsmEvidenceReportIn,
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
from keylime_openstack.services.opentcsm import opentcsm_report_to_evidence
from keylime_openstack.services.opentcsm_collect import OpenTcsmCollector
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
from keylime_openstack.services.trust_agents import (
    node_trust_agent_name,
    node_trust_agent_type,
    node_trusted_root,
)

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
def nodes(
    session: Session = Depends(db_session),
    settings: Settings = Depends(settings_dep),
) -> list[ComputeNodeOut]:
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
                "trust_agent_type": node_trust_agent_type(item, settings),
                "trust_agent_name": node_trust_agent_name(item, settings),
                "trusted_root": node_trusted_root(item, settings),
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
    return keylime_only_check(
        hosts=hosts,
        failures_only=failures_only,
        include_non_keylime=True,
    )


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
    current_policy_type = canonical_policy_type(policy.policy_type)
    if current_policy_type != POLICY_TPCM_DYNAMIC_MEASUREMENT and any(
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


@router.post("/nodes/{hostname}/opentcsm-evidence", dependencies=[Depends(require_admin)])
def ingest_opentcsm_evidence(
    hostname: str,
    report: OpenTcsmEvidenceReportIn,
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

    records = opentcsm_report_to_evidence(node, report_data, settings)
    for record in records:
        session.add(record)
    session.flush()
    session.add(
        AuditEvent(
            event_type="opentcsm_evidence_collect",
            target=node.hostname,
            severity="info" if all(item.status == "pass" for item in records) else "warning",
            message="collected OpenTCSM/Hygon TPCM evidence",
            event_details={
                "evidence_ids": [item.id for item in records],
                "statuses": {item.evidence_type: item.status for item in records},
                "provider": "opentcsm",
            },
        )
    )
    session.commit()
    return {
        "ok": True,
        "node": node.hostname,
        "provider": "opentcsm",
        "records": [
            {
                "evidence_id": item.id,
                "evidence_type": item.evidence_type,
                "status": item.status,
                "summary": item.summary,
            }
            for item in records
        ],
    }


@router.post("/nodes/{hostname}/opentcsm-collect")
def collect_opentcsm_evidence(
    hostname: str,
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
    try:
        result = OpenTcsmCollector(session, settings).collect(node)
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    session.commit()
    return result


@router.post("/nodes/{hostname}/opentcsm-access-check")
def check_opentcsm_access(
    hostname: str,
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
    agent_type = node_trust_agent_type(node, settings)
    if agent_type != TRUST_AGENT_OPENTCSM_TPCM:
        raise HTTPException(status_code=409, detail=f"node {node.hostname} does not use OpenTCSM/TPCM")

    target = node.hostname
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        result = OpenTcsmCollector(session, settings).collect(node)
    except Exception as exc:
        session.rollback()
        error = str(exc)
        checks = _opentcsm_failed_access_checks(error)
        session.add(
            AuditEvent(
                event_type="opentcsm_access_check",
                target=target,
                severity="error",
                message="OpenTCSM/TPCM node access check failed",
                event_details={"provider": "opentcsm", "checks": checks, "error": error},
            )
        )
        session.commit()
        return {
            "ok": False,
            "node": target,
            "provider": "opentcsm",
            "checked_at_utc": checked_at,
            "summary": "OpenTCSM/TPCM 接入检查未通过",
            "error": error,
            "checks": checks,
        }

    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    records = result.get("records") if isinstance(result.get("records"), list) else []
    command_errors = _opentcsm_command_errors(raw)
    evidence_ids = [
        item.get("evidence_id")
        for item in records
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    boot_enabled = raw.get("boot_measure_on") is True
    dynamic_enabled = raw.get("dynamic_measure_on") is True
    boot_pass = result.get("boot_status") == "pass"
    dynamic_pass = result.get("dynamic_measurement_status") == "pass"
    checks = [
        _access_check_item("SSH 连接", "pass", "节点 SSH 可达，远程采集任务已执行。"),
        _access_check_item(
            "OpenTCSM 命令",
            "pass" if not command_errors else "fail",
            "核心 OpenTCSM 命令可执行。" if not command_errors else "存在 OpenTCSM 命令执行失败。",
            "\n".join(command_errors),
        ),
        _access_check_item(
            "TPCM 身份",
            "pass" if raw.get("tpcm_id") else "fail",
            str(raw.get("tpcm_id") or "未读取到 TPCM ID。"),
        ),
        _access_check_item(
            "可信报告采集",
            "pass" if raw.get("trust_report_sha256") else "fail",
            f"报告指纹：{raw.get('trust_report_sha256')}" if raw.get("trust_report_sha256") else "未采集到可信报告。",
        ),
        _access_check_item(
            "启动度量",
            "pass" if boot_enabled and boot_pass else "fail",
            "启动度量已开启且可信状态通过。"
            if boot_enabled and boot_pass
            else f"启动度量开启：{_yes_no(boot_enabled)}，状态：{result.get('boot_status') or 'unknown'}。",
        ),
        _access_check_item(
            "动态度量",
            "pass" if dynamic_enabled and dynamic_pass else "fail",
            "动态度量已开启且可信状态通过。"
            if dynamic_enabled and dynamic_pass
            else f"动态度量开启：{_yes_no(dynamic_enabled)}，状态：{result.get('dynamic_measurement_status') or 'unknown'}。",
        ),
        _access_check_item(
            "证据入库",
            "pass" if evidence_ids else "fail",
            f"已写入 {len(evidence_ids)} 条可信证据。" if evidence_ids else "可信证据未写入管理数据库。",
        ),
    ]
    ok = all(item["status"] == "pass" for item in checks)
    session.add(
        AuditEvent(
            event_type="opentcsm_access_check",
            target=target,
            severity="info" if ok else "warning",
            message="OpenTCSM/TPCM node access check completed",
            event_details={
                "provider": "opentcsm",
                "checks": checks,
                "trusted": result.get("trusted"),
                "tpcm_id": raw.get("tpcm_id", ""),
                "evidence_ids": evidence_ids,
            },
        )
    )
    session.commit()
    return {
        "ok": ok,
        "node": target,
        "provider": "opentcsm",
        "checked_at_utc": checked_at,
        "summary": "OpenTCSM/TPCM 接入检查通过" if ok else "OpenTCSM/TPCM 接入检查未通过",
        "trusted": result.get("trusted"),
        "boot_status": result.get("boot_status"),
        "dynamic_measurement_status": result.get("dynamic_measurement_status"),
        "tpcm_id": raw.get("tpcm_id", ""),
        "evidence_ids": evidence_ids,
        "checks": checks,
    }


@router.get("/audit", response_model=list[AuditEventOut])
def audit(session: Session = Depends(db_session), limit: int = 100) -> list[AuditEventOut]:
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [AuditEventOut.model_validate(item) for item in rows]


@router.get("/traits")
def traits() -> dict[str, list[str]]:
    return {"traits": DEFAULT_TRUST_TRAITS}


def _access_check_item(
    name: str,
    status: str,
    summary: str,
    detail: str = "",
) -> dict[str, str]:
    return {
        "name": name,
        "status": status,
        "summary": summary,
        "detail": _truncate_detail(detail),
    }


def _opentcsm_failed_access_checks(error: str) -> list[dict[str, str]]:
    ssh_failed = _looks_like_ssh_error(error)
    return [
        _access_check_item(
            "SSH 连接",
            "fail" if ssh_failed else "pass",
            "无法通过 SSH 执行远程采集。" if ssh_failed else "SSH 已连接，但远程采集未完成。",
            error if ssh_failed else "",
        ),
        _access_check_item(
            "OpenTCSM 命令",
            "unknown" if ssh_failed else "fail",
            "SSH 未连通，未执行 OpenTCSM 命令。" if ssh_failed else "OpenTCSM 命令或采集脚本执行失败。",
            "" if ssh_failed else error,
        ),
        _access_check_item("TPCM 身份", "unknown", "未完成 TPCM ID 读取。"),
        _access_check_item("可信报告采集", "unknown", "未完成可信报告采集。"),
        _access_check_item("启动度量", "unknown", "未完成启动度量状态检查。"),
        _access_check_item("动态度量", "unknown", "未完成动态度量状态检查。"),
        _access_check_item("证据入库", "unknown", "未写入可信证据。"),
    ]


def _opentcsm_command_errors(raw: dict[str, Any]) -> list[str]:
    commands = raw.get("commands") if isinstance(raw.get("commands"), dict) else {}
    required = (
        "tpcm_info",
        "tpcm_id",
        "tpcm_features",
        "trust_status",
        "trust_report",
        "policy_report",
        "boot_measure_records",
        "global_control_policy",
    )
    errors: list[str] = []
    for name in required:
        item = commands.get(name)
        if not isinstance(item, dict):
            errors.append(f"{name}: 未返回执行结果")
            continue
        rc = int(item.get("rc") or 0)
        if rc == 0:
            continue
        stderr = str(item.get("stderr") or "").strip()
        stdout = str(item.get("stdout") or "").strip()
        detail = stderr or stdout or "无错误输出"
        errors.append(f"{name} rc={rc}: {_truncate_detail(detail, limit=300)}")
    return errors


def _looks_like_ssh_error(error: str) -> bool:
    text = error.lower()
    markers = (
        "unreachable",
        "host key verification",
        "permission denied",
        "connect to host",
        "connection timed out",
        "no route to host",
        "ssh:",
    )
    return any(marker in text for marker in markers)


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _truncate_detail(value: str, limit: int = 1600) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


def _record_policy_audit(
    session: Session,
    event_type: str,
    policy: TrustPolicy,
    message: str,
) -> None:
    policy_type = canonical_policy_type(policy.policy_type)
    details: dict[str, Any] = {
        "policy_id": policy.id,
        "policy_type": policy.policy_type,
        "status": policy.status,
    }
    if policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT:
        mapped_events = {
            "policy_create": "tpcm_dynamic_policy_save",
            "policy_update": "tpcm_dynamic_policy_save",
            "policy_deploy_queued": "tpcm_dynamic_policy_apply_queued",
        }
        event_type = mapped_events.get(event_type, event_type)
        details.update(_dynamic_policy_audit_details(policy, event_type))
        if event_type == "tpcm_dynamic_policy_apply_queued":
            message = "dynamic measurement policy apply queued"
        else:
            message = "dynamic measurement policy saved"
    session.add(
        AuditEvent(
            event_type=event_type,
            target=policy.name,
            severity="info",
            message=message,
            event_details=details,
        )
    )


def _dynamic_policy_audit_details(policy: TrustPolicy, event_type: str) -> dict[str, Any]:
    content = dict(policy.content or {})
    configs = content.get("environment_object_configs") if isinstance(content.get("environment_object_configs"), dict) else {}
    enabled_objects = [
        name
        for name, config in configs.items()
        if isinstance(config, dict) and config.get("enabled") is True
    ]
    if not enabled_objects:
        enabled_objects = [str(item) for item in content.get("environment_objects") or []]
    target_names = [
        str(dict(binding.binding_details or {}).get("hostname") or binding.target_id)
        for binding in policy.bindings
        if binding.active
    ]
    return {
        "log_type": "dynamic_measurement",
        "subject_name": "TPCM",
        "object_name": ",".join(enabled_objects) if enabled_objects else "none",
        "operation": "策略生效" if event_type == "tpcm_dynamic_policy_apply_queued" else "策略保存",
        "result": "已入队" if event_type == "tpcm_dynamic_policy_apply_queued" else "成功",
        "target_nodes": target_names,
        "environment_object_configs": configs,
        "environment_interval_milli": content.get("environment_interval_milli"),
        "hash": "",
    }


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
