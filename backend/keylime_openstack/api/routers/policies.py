"""Trust policy management API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.api.deps import db_session, require_admin, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.constants import POLICY_TPCM_DYNAMIC_MEASUREMENT
from keylime_openstack.models import AuditEvent, ComputeNode, PolicyBinding, TrustPolicy
from keylime_openstack.schemas import (
    TpcmDynamicGlobalSwitchIn,
    TrustPolicyIn,
    TrustPolicyOut,
)
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.policy import (
    bind_policy_to_nodes,
    canonical_policy_type,
    list_policies,
    load_policy,
    policy_out,
    validated_policy_payload,
)
from keylime_openstack.services.tasks import create_task

router = APIRouter()


@router.get("/policies", response_model=list[TrustPolicyOut])
def policies(session: Session = Depends(db_session)) -> list[TrustPolicyOut]:
    return [policy_out(session, item) for item in list_policies(session)]


@router.post("/policies/tpcm-dynamic/global-switch", dependencies=[Depends(require_admin)])
def set_tpcm_dynamic_global_switch(
    request: TpcmDynamicGlobalSwitchIn,
    session: Session = Depends(db_session),
) -> dict[str, object]:
    ensure_default_environment(session)
    operation = "开启集群动态度量" if request.enabled else "关闭集群动态度量"
    session.add(
        AuditEvent(
            event_type="tpcm_dynamic_global_switch",
            target="OpenTCSM 动态度量",
            severity="info",
            message="updated TPCM dynamic measurement global switch",
            event_details={
                "log_type": "dynamic_measurement",
                "subject_name": "TPCM",
                "object_name": "全部动态度量对象",
                "measurement_type": "全局控制",
                "measurement_baseline": "",
                "operation": operation,
                "result": "成功",
                "global_dynamic_measure_enabled": request.enabled,
                "hash": "",
            },
        )
    )
    session.commit()
    return {
        "ok": True,
        "enabled": request.enabled,
        "message": "TPCM 动态度量全局控制已更新",
    }


@router.get("/policies/tpcm-dynamic/global-switch")
def get_tpcm_dynamic_global_switch(
    session: Session = Depends(db_session),
) -> dict[str, object]:
    return _tpcm_dynamic_global_control(session)


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
    policy_type = canonical_policy_type(payload["policy_type"])
    if policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT and not _tpcm_dynamic_global_enabled(session):
        deploy_now = False
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
    if current_policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT and not _tpcm_dynamic_global_enabled(session):
        deploy_now = False
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
    if (
        canonical_policy_type(policy.policy_type) == POLICY_TPCM_DYNAMIC_MEASUREMENT
        and not _tpcm_dynamic_global_enabled(session)
    ):
        raise HTTPException(status_code=409, detail="TPCM 动态度量全局关闭，节点配置暂不生效")
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
    if (
        canonical_policy_type(policy.policy_type) == POLICY_TPCM_DYNAMIC_MEASUREMENT
        and not _tpcm_dynamic_global_enabled(session)
    ):
        raise HTTPException(status_code=409, detail="TPCM 动态度量全局关闭，节点配置暂不生效")
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


def _tpcm_dynamic_global_enabled(session: Session) -> bool:
    return bool(_tpcm_dynamic_global_control(session)["enabled"])


def _tpcm_dynamic_global_control(session: Session) -> dict[str, object]:
    event = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == "tpcm_dynamic_global_switch")
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).first()
    if not event:
        return {
            "ok": True,
            "enabled": True,
            "source": "default",
            "updated_at": None,
            "event_id": None,
        }
    details = dict(event.event_details or {})
    return {
        "ok": True,
        "enabled": details.get("global_dynamic_measure_enabled") is not False,
        "source": "audit_event",
        "updated_at": event.created_at.isoformat() if event.created_at else None,
        "event_id": event.id,
    }


def _dynamic_policy_audit_details(policy: TrustPolicy, event_type: str) -> dict[str, Any]:
    content = dict(policy.content or {})
    node_dynamic_measure_enabled = bool(
        content.get(
            "node_dynamic_measure_enabled",
            content.get("dynamic_measure_required", True),
        )
    )
    configs = content.get("environment_object_configs") if isinstance(content.get("environment_object_configs"), dict) else {}
    enabled_objects = [
        name
        for name, config in configs.items()
        if node_dynamic_measure_enabled
        and isinstance(config, dict)
        and config.get("enabled") is True
    ]
    if node_dynamic_measure_enabled and not enabled_objects:
        enabled_objects = [str(item) for item in content.get("environment_objects") or []]
    target_names = [
        str(dict(binding.binding_details or {}).get("hostname") or binding.target_id)
        for binding in policy.bindings
        if binding.active
    ]
    return {
        "log_type": "dynamic_measurement",
        "subject_name": "TPCM",
        "object_name": ",".join(enabled_objects)
        if enabled_objects
        else "全部动态度量对象"
        if not node_dynamic_measure_enabled
        else "none",
        "measurement_type": "策略生效" if event_type == "tpcm_dynamic_policy_apply_queued" else "策略保存",
        "measurement_baseline": "",
        "operation": "策略生效" if event_type == "tpcm_dynamic_policy_apply_queued" else "策略保存",
        "result": "已入队" if event_type == "tpcm_dynamic_policy_apply_queued" else "成功",
        "target_nodes": target_names,
        "node_dynamic_measure_enabled": node_dynamic_measure_enabled,
        "environment_object_configs": configs,
        "environment_interval_milli": content.get("environment_interval_milli"),
        "hash": "",
    }
