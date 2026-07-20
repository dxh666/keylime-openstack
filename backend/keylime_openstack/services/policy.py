"""Policy validation, node binding, and response helpers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from keylime_openstack.constants import (
    BINDING_NODE,
    LEGACY_POLICY_TYPE_ALIASES,
    POLICY_IMA_RUNTIME,
    POLICY_MEASURED_BOOT,
    POLICY_DEPLOY_NOT_DEPLOYED,
    POLICY_DEPLOY_QUEUED,
    SUPPORTED_POLICY_TYPES,
)
from keylime_openstack.models import ComputeNode, PolicyBinding, TrustPolicy
from keylime_openstack.schemas import PolicyBindingOut, TrustPolicyIn, TrustPolicyOut


def canonical_policy_type(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    return LEGACY_POLICY_TYPE_ALIASES.get(normalized, normalized)


def validated_policy_payload(policy_in: TrustPolicyIn) -> tuple[dict[str, Any], list[int], bool]:
    payload = policy_in.model_dump()
    target_node_ids = sorted(set(payload.pop("target_node_ids")))
    deploy_now = bool(payload.pop("deploy_now"))
    policy_type = canonical_policy_type(str(payload["policy_type"]))
    if policy_type not in SUPPORTED_POLICY_TYPES:
        raise HTTPException(status_code=422, detail=f"不支持的策略类型：{policy_type}")
    if not target_node_ids:
        raise HTTPException(status_code=422, detail="请至少选择一个目标节点")

    content = dict(payload.get("content") or {})
    if policy_type == POLICY_MEASURED_BOOT:
        content = _validate_measured_boot(content)
    elif policy_type == POLICY_IMA_RUNTIME:
        content = _validate_ima_runtime(content)

    payload["policy_type"] = policy_type
    payload["content"] = content
    payload["source"] = {
        **dict(payload.get("source") or {}),
        "executor": "ansible",
        "managed_by": "keylime-openstack",
    }
    return payload, target_node_ids, deploy_now


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
    pcrs = content.get("pcrs", list(range(8)))
    if not isinstance(pcrs, list) or not pcrs:
        raise HTTPException(status_code=422, detail="可信启动 PCR 范围不能为空")
    try:
        normalized_pcrs = sorted({int(value) for value in pcrs})
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="可信启动 PCR 编号必须是整数",
        ) from exc
    if normalized_pcrs[0] < 0 or normalized_pcrs[-1] > 23:
        raise HTTPException(
            status_code=422,
            detail="可信启动 PCR 编号必须在 0 到 23 之间",
        )
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
        "reference_state_mode": "provided" if reference_state else "collect_from_node",
        "secure_boot_required": bool(content.get("secure_boot_required", True)),
    }


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
