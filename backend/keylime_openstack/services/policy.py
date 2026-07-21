"""Policy validation, node binding, and response helpers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from keylime_openstack.constants import (
    BINDING_NODE,
    LEGACY_POLICY_TYPE_ALIASES,
    POLICY_EVM,
    POLICY_IMA_RUNTIME,
    POLICY_MEASURED_BOOT,
    POLICY_DEPLOY_NOT_DEPLOYED,
    POLICY_DEPLOY_QUEUED,
    POLICY_TPCM_DYNAMIC_MEASUREMENT,
    SUPPORTED_POLICY_TYPES,
)
from keylime_openstack.models import ComputeNode, PolicyBinding, TrustPolicy
from keylime_openstack.services.opentcsm_policy import (
    OPEN_TCSM_DMEASURE_OBJECTS,
    normalize_dmeasure_objects,
)
from keylime_openstack.schemas import PolicyBindingOut, TrustPolicyIn, TrustPolicyOut


def canonical_policy_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    return LEGACY_POLICY_TYPE_ALIASES.get(normalized, normalized)


def validated_policy_payload(policy_in: TrustPolicyIn) -> tuple[dict[str, Any], list[int], bool]:
    payload = policy_in.model_dump()
    target_node_ids = sorted(set(payload.pop("target_node_ids")))
    deploy_now = bool(payload.pop("deploy_now"))
    policy_type = canonical_policy_type(str(payload["policy_type"]))
    if policy_type == POLICY_EVM:
        raise HTTPException(status_code=422, detail="EVM 策略管理尚未启用")
    if policy_type not in SUPPORTED_POLICY_TYPES:
        raise HTTPException(status_code=422, detail=f"不支持的策略类型：{policy_type}")
    if not target_node_ids:
        raise HTTPException(status_code=422, detail="请至少选择一个目标节点")
    if policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT and len(target_node_ids) != 1:
        raise HTTPException(
            status_code=422,
            detail="环境动态度量策略必须按单个节点独立管理",
        )

    content = dict(payload.get("content") or {})
    if policy_type == POLICY_MEASURED_BOOT:
        content = _validate_measured_boot(content)
    elif policy_type == POLICY_IMA_RUNTIME:
        content = _validate_ima_runtime(content)
    elif policy_type == POLICY_TPCM_DYNAMIC_MEASUREMENT:
        content = _validate_tpcm_dynamic_measurement(content)

    payload["policy_type"] = policy_type
    payload["content"] = content
    payload["source"] = {
        **dict(payload.get("source") or {}),
        "executor": "ansible",
        "managed_by": "keylime-openstack",
    }
    return payload, target_node_ids, deploy_now


def normalized_tpcm_dynamic_measurement_content(content: dict[str, Any]) -> dict[str, Any]:
    return _validate_tpcm_dynamic_measurement(dict(content or {}))


def bind_policy_to_nodes(
    session: Session,
    policy: TrustPolicy,
    node_ids: list[int],
    *,
    deploy_now: bool,
) -> list[PolicyBinding]:
    nodes = session.scalars(
        select(ComputeNode)
        .where(ComputeNode.id.in_(node_ids))
        .where(ComputeNode.role == "compute")
        .where(ComputeNode.enabled.is_(True))
        .order_by(ComputeNode.hostname)
    ).all()
    if len(nodes) != len(node_ids):
        found = {node.id for node in nodes}
        missing = [node_id for node_id in node_ids if node_id not in found]
        raise HTTPException(status_code=422, detail=f"计算节点不存在或已停用：{missing}")

    bindings: list[PolicyBinding] = []
    for node in nodes:
        binding = PolicyBinding(
            policy=policy,
            target_type=BINDING_NODE,
            target_id=node.id,
            active=True,
            executor="ansible",
            application_status=(
                POLICY_DEPLOY_QUEUED if deploy_now else POLICY_DEPLOY_NOT_DEPLOYED
            ),
            binding_details={
                "hostname": node.hostname,
                "management_ip": node.management_ip,
                "keylime_agent_uuid": node.keylime_agent_uuid,
            },
        )
        session.add(binding)
        bindings.append(binding)
    session.flush()
    return bindings


def load_policy(session: Session, policy_id: int) -> TrustPolicy | None:
    return session.scalars(
        select(TrustPolicy)
        .options(selectinload(TrustPolicy.bindings))
        .where(TrustPolicy.id == policy_id)
        .execution_options(populate_existing=True)
    ).first()


def list_policies(session: Session) -> list[TrustPolicy]:
    return session.scalars(
        select(TrustPolicy)
        .options(selectinload(TrustPolicy.bindings))
        .order_by(TrustPolicy.policy_type, TrustPolicy.name)
    ).all()


def policy_out(session: Session, policy: TrustPolicy) -> TrustPolicyOut:
    node_ids = [
        binding.target_id
        for binding in policy.bindings
        if binding.target_type == BINDING_NODE
    ]
    node_names = dict(
        session.execute(
            select(ComputeNode.id, ComputeNode.hostname).where(ComputeNode.id.in_(node_ids))
        ).all()
    ) if node_ids else {}
    bindings = [
        PolicyBindingOut(
            id=binding.id,
            target_type=binding.target_type,
            target_id=binding.target_id,
            target_name=node_names.get(binding.target_id, str(binding.target_id)),
            active=binding.active,
            executor=binding.executor,
            application_status=binding.application_status,
            external_policy_name=binding.external_policy_name,
            applied_at=binding.applied_at,
            last_error=binding.last_error,
            binding_details=binding.binding_details or {},
            keylime_policy=_binding_keylime_policy(binding),
        )
        for binding in sorted(policy.bindings, key=lambda item: (item.target_type, item.target_id))
    ]
    return TrustPolicyOut(
        id=policy.id,
        name=policy.name,
        policy_type=canonical_policy_type(policy.policy_type),
        version=policy.version,
        status=policy.status,
        hash_alg=policy.hash_alg,
        content=policy.content,
        protected_paths=policy.protected_paths,
        excludes=policy.excludes,
        source=policy.source,
        description=policy.description,
        bindings=bindings,
    )


def effective_policies_for_node(session: Session, node: ComputeNode) -> list[TrustPolicy]:
    """Return active node-specific policies ordered by priority."""

    bindings = session.scalars(
        select(PolicyBinding)
        .where(PolicyBinding.active.is_(True))
        .where(PolicyBinding.target_type == BINDING_NODE)
        .where(PolicyBinding.target_id == node.id)
        .order_by(PolicyBinding.priority)
    ).all()
    return [binding.policy for binding in bindings]


def _validate_measured_boot(content: dict[str, Any]) -> dict[str, Any]:
    normalized_pcrs = _normalize_pcr_list(content.get("pcrs", list(range(8))))
    fallback_pcrs = _normalize_pcr_list(content.get("fallback_pcrs", [7]))
    policy_engine = str(content.get("policy_engine") or "").strip()
    if policy_engine == "accept-all":
        raise HTTPException(
            status_code=422,
            detail="可信启动策略不允许使用 accept-all",
        )
    reference_state = content.get("reference_state")
    if reference_state is not None and not isinstance(reference_state, dict):
        raise HTTPException(
            status_code=422,
            detail="可信启动参考状态必须是 JSON 对象",
        )
    return {
        **content,
        "policy_engine": policy_engine or "configured",
        "pcrs": normalized_pcrs,
        "fallback_pcrs": fallback_pcrs,
        "reference_state_mode": "provided" if reference_state else "collect_from_node",
        "event_log_fallback": str(content.get("event_log_fallback") or "pcr_quote"),
        "secure_boot_required": bool(content.get("secure_boot_required", True)),
    }


def _normalize_pcr_list(value: Any) -> list[int]:
    if not isinstance(value, list) or not value:
        raise HTTPException(status_code=422, detail="可信启动 PCR 范围不能为空")
    try:
        normalized = sorted({int(item) for item in value})
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="可信启动 PCR 编号必须是整数",
        ) from exc
    if normalized[0] < 0 or normalized[-1] > 23:
        raise HTTPException(
            status_code=422,
            detail="可信启动 PCR 编号必须在 0 到 23 之间",
        )
    return normalized


def _validate_ima_runtime(content: dict[str, Any]) -> dict[str, Any]:
    node_policy = str(content.get("node_ima_policy") or "").strip()
    if not node_policy:
        raise HTTPException(status_code=422, detail="节点 IMA 度量策略不能为空")
    if not any(line.strip().startswith("measure ") for line in node_policy.splitlines()):
        raise HTTPException(
            status_code=422,
            detail="节点 IMA 策略至少需要一条 measure 规则",
        )
    return {
        **content,
        "node_ima_policy": node_policy + "\n",
        "runtime_policy_generation": "keylime-policy-from-measurements",
        "reboot_after_apply": bool(content.get("reboot_after_apply", False)),
    }


def _validate_tpcm_dynamic_measurement(content: dict[str, Any]) -> dict[str, Any]:
    node_dynamic_measure_enabled = bool(
        content.get(
            "node_dynamic_measure_enabled",
            content.get("dynamic_measure_required", True),
        )
    )
    environment_interval_milli = _bounded_int(
        content.get("environment_interval_milli", content.get("interval_milli", 60000)),
        "动态度量周期",
        minimum=1000,
        maximum=86_400_000,
    )
    environment_object_configs = _validate_dmeasure_object_configs(
        content,
        default_interval_milli=environment_interval_milli,
    )
    environment_objects = [
        name
        for name, config in environment_object_configs.items()
        if node_dynamic_measure_enabled and config["enabled"]
    ]
    auth_material_ref = str(content.get("auth_material_ref") or "dmeasure-uid").strip()
    if not auth_material_ref:
        raise HTTPException(status_code=422, detail="OpenTCSM 授权材料不能为空")
    return {
        **content,
        "trust_agent_type": "opentcsm_tpcm",
        "policy_scope": "tpcm_dynamic_measurement",
        "require_clean_trust_report": bool(content.get("require_clean_trust_report", False)),
        "node_dynamic_measure_enabled": node_dynamic_measure_enabled,
        "dynamic_measure_required": node_dynamic_measure_enabled,
        "environment_object_configs": environment_object_configs,
        "environment_objects": environment_objects,
        "environment_interval_milli": environment_interval_milli,
        "delete_unmanaged_objects": False,
        "minimum_dynamic_baselines": 0,
        "auth_material_ref": auth_material_ref,
        "keylime_artifact": "opentcsm_dynamic_measurement_policy",
    }


def _validate_dmeasure_object_configs(
    content: dict[str, Any],
    *,
    default_interval_milli: int,
) -> dict[str, dict[str, int | bool]]:
    raw_configs = content.get("environment_object_configs")
    configs: dict[str, dict[str, int | bool]] = {}
    if isinstance(raw_configs, dict):
        for name in OPEN_TCSM_DMEASURE_OBJECTS:
            raw = raw_configs.get(name) or {}
            enabled = bool(raw.get("enabled", False))
            interval = _bounded_int(
                raw.get("interval_milli", raw.get("intervalMilli", default_interval_milli)),
                f"{name} 动态度量周期",
                minimum=1000,
                maximum=86_400_000,
            )
            configs[name] = {"enabled": enabled, "interval_milli": interval}
        return configs

    try:
        enabled_objects = normalize_dmeasure_objects(
            content.get("environment_objects", list(OPEN_TCSM_DMEASURE_OBJECTS))
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for name in OPEN_TCSM_DMEASURE_OBJECTS:
        configs[name] = {
            "enabled": name in enabled_objects,
            "interval_milli": default_interval_milli,
        }
    return configs


def _bounded_int(value: Any, label: str, *, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"{label}必须是整数") from exc
    if normalized < minimum or normalized > maximum:
        raise HTTPException(status_code=422, detail=f"{label}必须在 {minimum} 到 {maximum} 之间")
    return normalized


def _binding_keylime_policy(binding: PolicyBinding) -> dict[str, Any]:
    details = dict(binding.binding_details or {})
    keylime_policy = {
        "name": binding.external_policy_name,
        "artifact": details.get("keylime_artifact", ""),
        "content_sha256": details.get("rendered_policy_sha256", ""),
        "evidence_sha256": details.get("evidence_sha256", ""),
        "evidence_type": details.get("evidence_type", ""),
        "generated_at": details.get("generated_at", ""),
        "deployed_by": details.get("deployed_by", ""),
    }
    return {key: value for key, value in keylime_policy.items() if value not in ("", None)}
