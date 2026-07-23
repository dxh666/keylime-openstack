"""Policy API orchestration services."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import POLICY_MEASURED_BOOT, POLICY_TPCM_DYNAMIC_MEASUREMENT
from keylime_openstack.models import AuditEvent, ComputeNode, PolicyBinding, TrustPolicy
from keylime_openstack.schemas import (
    TpcmDynamicGlobalSwitchIn,
    TrustPolicyIn,
    TrustPolicyOut,
)
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.audit import record_audit_event
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.policy import (
    bind_policy_to_nodes,
    canonical_policy_type,
    list_policies,
    load_policy,
    policy_out,
    validated_policy_payload,
)
from keylime_openstack.services.tasks import create_task, mark_failed, mark_running, mark_success
from keylime_openstack.services.tpcm_boot_hardware_apply import (
    TpcmBootHardwareApplyError,
    TpcmBootHardwareApplyService,
    _opentcsm_boot_failure_details,
)


def list_policy_responses(session: Session) -> list[TrustPolicyOut]:
    return [policy_out(session, item) for item in list_policies(session)]


def set_tpcm_dynamic_global_switch(
    request: TpcmDynamicGlobalSwitchIn,
    session: Session,
) -> dict[str, object]:
    ensure_default_environment(session)
    operation = "开启集群动态度量" if request.enabled else "关闭集群动态度量"
    record_audit_event(
        session,
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
    session.commit()
    return {
        "ok": True,
        "enabled": request.enabled,
        "message": "TPCM 动态度量全局控制已更新",
    }


def get_tpcm_dynamic_global_switch(
    session: Session,
) -> dict[str, object]:
    return _tpcm_dynamic_global_control(session)


def get_policy_response(policy_id: int, session: Session) -> TrustPolicyOut:
    policy = load_policy(session, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"策略不存在：{policy_id}")
    return policy_out(session, policy)


def create_policy(
    policy_in: TrustPolicyIn,
    session: Session,
    settings: Settings,
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
    bind_policy_to_nodes(session, policy, node_ids, deploy_now=deploy_now, settings=settings)
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


def update_policy(
    policy_id: int,
    policy_in: TrustPolicyIn,
    session: Session,
    settings: Settings,
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
    bind_policy_to_nodes(session, policy, node_ids, deploy_now=deploy_now, settings=settings)
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


def deploy_policy(policy_id: int, session: Session) -> dict[str, object]:
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


def deploy_policy_binding(
    policy_id: int,
    binding_id: int,
    session: Session,
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


def apply_tpcm_boot_hardware(
    policy_id: int,
    binding_id: int,
    session: Session,
    settings: Settings,
) -> dict[str, object]:
    policy = load_policy(session, policy_id)
    if not policy:
        raise HTTPException(status_code=404, detail=f"策略不存在：{policy_id}")
    binding = session.get(PolicyBinding, binding_id)
    if not binding or binding.policy_id != policy.id or not binding.active:
        raise HTTPException(status_code=404, detail=f"策略绑定不存在或已失效：{binding_id}")
    node = session.get(ComputeNode, binding.target_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"计算节点不存在：{binding.target_id}")

    target = f"{policy.name}:{node.hostname}"
    task = create_task(
        session,
        "tpcm_boot_hardware_apply",
        target=target,
        requested_by="api",
        task_args={
            "policy_id": policy.id,
            "binding_id": binding.id,
            "node": node.hostname,
            "policy_type": policy.policy_type,
        },
    )
    mark_running(task)
    try:
        result = TpcmBootHardwareApplyService(session, settings).apply(policy, binding, node)
    except TpcmBootHardwareApplyError as exc:
        error = str(exc)
        if exc.details:
            binding.binding_details = {
                **dict(binding.binding_details or {}),
                **exc.details,
            }
        if not exc.details.get("tpcm_boot_hardware_audit_recorded"):
            record_audit_event(
                session,
                event_type="tpcm_boot_hardware_apply",
                target=target,
                severity="warning",
                message="TPCM trusted boot hardware apply failed",
                event_details={
                    "log_type": "trusted_boot",
                    "policy_id": policy.id,
                    "binding_id": binding.id,
                    "subject_name": "TPCM",
                    "object_name": node.hostname,
                    "measurement_type": "hardware_apply",
                    "measurement_baseline": dict(binding.binding_details or {}).get(
                        "rendered_policy_sha256",
                        "",
                    ),
                    "operation": "write_boot_references_and_enable_control",
                    "result": "failed",
                    **exc.details,
                },
            )
        mark_failed(task, error, {"ok": False, **exc.details})
        session.commit()
        return {"ok": False, "task_id": task.id, "error": error, "details": exc.details}
    except Exception as exc:  # pragma: no cover - remote execution boundary
        error = str(exc)
        details = _opentcsm_boot_failure_details(error)
        binding.binding_details = {
            **dict(binding.binding_details or {}),
            **details,
        }
        record_audit_event(
            session,
            event_type="tpcm_boot_hardware_apply",
            target=target,
            severity="warning",
            message="TPCM trusted boot hardware apply failed",
            event_details={
                "log_type": "trusted_boot",
                "policy_id": policy.id,
                "binding_id": binding.id,
                "subject_name": "TPCM",
                "object_name": node.hostname,
                "measurement_type": "hardware_apply",
                "measurement_baseline": dict(binding.binding_details or {}).get(
                    "rendered_policy_sha256",
                    "",
                ),
                "operation": "write_boot_references_and_enable_control",
                "result": "failed",
                **details,
            },
        )
        mark_failed(task, error, {"ok": False, **details})
        session.commit()
        return {"ok": False, "task_id": task.id, "error": error, "details": details}
    mark_success(task, result)
    session.commit()
    return {"ok": True, "task_id": task.id, "result": result}


def delete_policy(
    policy_id: int,
    session: Session,
    settings: Settings,
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
    record_audit_event(
        session,
        event_type="policy_delete",
        target=policy_name,
        severity="info",
        message="deleted trust policy",
        event_details={"policy_id": policy_id, "policy_type": policy_type},
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
    elif policy_type == POLICY_MEASURED_BOOT:
        details.update(_trusted_boot_policy_audit_details(policy, event_type))
        if event_type == "policy_deploy_queued":
            event_type = "trusted_boot_policy_apply_queued"
            message = "trusted boot policy apply queued"
        else:
            event_type = "trusted_boot_policy_save"
            message = "trusted boot policy saved"
    record_audit_event(
        session,
        event_type=event_type,
        target=policy.name,
        severity="info",
        message=message,
        event_details=details,
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


def _trusted_boot_policy_audit_details(policy: TrustPolicy, event_type: str) -> dict[str, Any]:
    content = dict(policy.content or {})
    trusted_root_type = str(content.get("trusted_root_type") or "tpm").upper()
    target_names = [
        str(dict(binding.binding_details or {}).get("hostname") or binding.target_id)
        for binding in policy.bindings
        if binding.active
    ]
    baseline = (
        content.get("expected_boot_records_sha256")
        or content.get("expected_trust_report_sha256")
        or ""
    )
    return {
        "log_type": "trusted_boot",
        "subject_name": trusted_root_type,
        "object_name": ",".join(target_names) if target_names else "-",
        "measurement_type": "策略生效" if event_type == "policy_deploy_queued" else "策略保存",
        "measurement_baseline": baseline,
        "operation": "策略生效" if event_type == "policy_deploy_queued" else "策略保存",
        "result": "已入队" if event_type == "policy_deploy_queued" else "成功",
        "target_nodes": target_names,
        "trusted_root_type": str(content.get("trusted_root_type") or "tpm"),
        "hash": baseline,
    }
