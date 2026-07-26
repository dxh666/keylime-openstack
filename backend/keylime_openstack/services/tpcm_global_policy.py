"""System-level OpenTCSM/TPCM global policy control."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from keylime_openstack.config import Settings
from keylime_openstack.constants import ADAPTER_OPENTCSM, PROVIDER_OPENTCSM, TRUST_ROOT_TPCM
from keylime_openstack.models import AuditEvent, ComputeNode, EvidenceRecord
from keylime_openstack.services.ansible import AnsibleExecutor
from keylime_openstack.services.audit import record_audit_event
from keylime_openstack.services.opentcsm_policy import (
    load_opentcsm_auth_material,
    opentcsm_auth_ref_for_profile,
)
from keylime_openstack.services.tasks import create_task
from keylime_openstack.services.trust_registration import ensure_trusted_node_profile


GLOBAL_POLICY_FIELDS: dict[str, dict[str, Any]] = {
    "boot_measure_on": {
        "id": 1,
        "label": "启动度量",
        "value_type": "bool",
        "purpose": "boot",
        "writable": True,
    },
    "boot_control": {
        "id": 2,
        "label": "启动控制",
        "value_type": "bool",
        "purpose": "boot",
        "writable": True,
        "risk": "high",
    },
    "program_measure_on": {
        "id": 3,
        "label": "程序/文件完整性度量",
        "value_type": "bool",
        "purpose": "file_integrity",
        "writable": False,
    },
    "program_control": {
        "id": 4,
        "label": "程序/文件完整性控制",
        "value_type": "bool",
        "purpose": "file_integrity",
        "writable": False,
    },
    "program_measure_mode": {
        "id": 5,
        "label": "程序度量模式",
        "value_type": "int",
        "purpose": "file_integrity",
        "writable": False,
    },
    "program_measure_match_mode": {
        "id": 6,
        "label": "程序基准匹配方式",
        "value_type": "int",
        "purpose": "file_integrity",
        "writable": False,
    },
    "dynamic_measure_on": {
        "id": 7,
        "label": "动态度量",
        "value_type": "bool",
        "purpose": "dynamic",
        "writable": True,
    },
    "dmeasure_max_busy_delay": {
        "id": 8,
        "label": "动态度量忙等待上限",
        "value_type": "int",
        "purpose": "dynamic",
        "writable": True,
        "unit": "秒",
        "min": 0,
        "max": 86400,
    },
    "process_dmeasure_interval": {
        "id": 9,
        "label": "进程动态度量间隔",
        "value_type": "int",
        "purpose": "process_dynamic",
        "writable": False,
        "unit": "毫秒",
    },
    "process_dmeasure_ref_mode": {
        "id": 10,
        "label": "进程动态度量基准来源",
        "value_type": "int",
        "purpose": "process_dynamic",
        "writable": False,
    },
    "process_dmeasure_match_mode": {
        "id": 11,
        "label": "进程动态度量匹配方式",
        "value_type": "int",
        "purpose": "process_dynamic",
        "writable": False,
    },
    "process_dmeasure_sub_process_mode": {
        "id": 12,
        "label": "子进程度量模式",
        "value_type": "int",
        "purpose": "process_dynamic",
        "writable": False,
    },
    "process_dmeasure_old_process_mode": {
        "id": 13,
        "label": "旧进程度量模式",
        "value_type": "int",
        "purpose": "process_dynamic",
        "writable": False,
    },
    "process_dmeasure_lib_mode": {
        "id": 14,
        "label": "进程库度量模式",
        "value_type": "int",
        "purpose": "process_dynamic",
        "writable": False,
    },
    "process_verify_lib_mode": {
        "id": 15,
        "label": "进程库验证方式",
        "value_type": "int",
        "purpose": "process_dynamic",
        "writable": False,
    },
    "measure_use_cache": {
        "id": 16,
        "label": "度量缓存",
        "value_type": "bool",
        "purpose": "runtime",
        "writable": False,
    },
    "tsb_flag1": {
        "id": 17,
        "label": "TSB 检查标志 1",
        "value_type": "bool",
        "purpose": "tsb",
        "writable": False,
    },
    "tsb_flag2": {
        "id": 18,
        "label": "TSB 检查标志 2",
        "value_type": "bool",
        "purpose": "tsb",
        "writable": False,
    },
    "tsb_flag3": {
        "id": 19,
        "label": "TSB 控制标志 3",
        "value_type": "bool",
        "purpose": "tsb",
        "writable": False,
    },
}


def parse_global_control_policy(text: str) -> dict[str, dict[str, Any]]:
    """Parse get_global_control_policy output into known policy fields."""

    result: dict[str, dict[str, Any]] = {}
    for field, meta in GLOBAL_POLICY_FIELDS.items():
        raw = _field_value_text(text, field)
        if raw is None:
            continue
        value = _normalize_policy_value(raw, str(meta["value_type"]))
        result[field] = {
            "id": meta["id"],
            "label": meta["label"],
            "value": value,
            "raw": raw,
            "writable": bool(meta.get("writable")),
            "unit": meta.get("unit") or "",
            "risk": meta.get("risk") or "",
        }
    return result


def get_tpcm_global_policy_state(session: Session, settings: Settings) -> dict[str, Any]:
    """Return product-level system global policy state from latest TPCM evidence."""

    nodes = _managed_tpcm_nodes(session, settings)
    node_states = []
    for node, _profile in nodes:
        record = _latest_opentcsm_record(session, node)
        raw = {}
        if record and isinstance(record.payload, dict):
            raw = record.payload.get("raw") if isinstance(record.payload.get("raw"), dict) else {}
        policy = (
            raw.get("global_control_policy")
            if isinstance(raw.get("global_control_policy"), dict)
            else {}
        )
        node_states.append(
            {
                "hostname": node.hostname,
                "collected_at": (
                    record.collected_at.isoformat() if record and record.collected_at else None
                ),
                "fields": policy,
            }
        )

    fields: dict[str, dict[str, Any]] = {}
    for field, meta in GLOBAL_POLICY_FIELDS.items():
        values = [
            item["fields"].get(field, {}).get("value")
            for item in node_states
            if isinstance(item.get("fields"), dict) and field in item["fields"]
        ]
        known = [value for value in values if value is not None]
        enabled_count = sum(1 for value in known if value is True)
        disabled_count = sum(1 for value in known if value is False)
        unknown_count = len(nodes) - len(known)
        aggregate = known[0] if known and all(value == known[0] for value in known) else None
        fields[field] = {
            "id": meta["id"],
            "label": meta["label"],
            "value_type": meta["value_type"],
            "writable": bool(meta.get("writable")),
            "unit": meta.get("unit") or "",
            "risk": meta.get("risk") or "",
            "value": aggregate,
            "known_nodes": len(known),
            "unknown_nodes": max(unknown_count, 0),
            "enabled_nodes": enabled_count,
            "disabled_nodes": disabled_count,
        }

    latest_request = _latest_global_policy_request(session)
    return {
        "ok": True,
        "nodes_total": len(nodes),
        "fields": fields,
        "nodes": node_states,
        "last_request": latest_request,
    }


def queue_tpcm_global_policy_apply(
    session: Session,
    fields: dict[str, Any],
    *,
    requested_by: str = "api",
) -> dict[str, Any]:
    updates = validate_global_policy_updates(fields)
    task = create_task(
        session,
        "tpcm_global_policy_apply",
        target="OpenTCSM TPCM global policy",
        requested_by=requested_by,
        task_args={"fields": updates},
    )
    record_audit_event(
        session,
        event_type="tpcm_global_policy_apply_queued",
        target="OpenTCSM TPCM global policy",
        severity="info",
        message="queued TPCM global policy apply task",
        event_details={
            "log_type": "tpcm_global_policy",
            "subject_name": "TPCM",
            "object_name": "全局策略",
            "operation": "全局策略下发",
            "result": "已入队",
            "fields": updates,
            "task_id": task.id,
        },
    )
    return {"ok": True, "task_id": task.id, "fields": updates, "message": "TPCM 全局策略下发任务已进入队列"}


def validate_global_policy_updates(fields: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(fields, dict) or not fields:
        raise ValueError("TPCM global policy update fields cannot be empty")
    updates: dict[str, Any] = {}
    for field, value in fields.items():
        name = str(field or "").strip()
        meta = GLOBAL_POLICY_FIELDS.get(name)
        if not meta:
            raise ValueError(f"unsupported TPCM global policy field: {name}")
        if not meta.get("writable"):
            raise ValueError(f"TPCM global policy field is read-only in this stage: {name}")
        updates[name] = _coerce_update_value(name, value, meta)
    return updates


class TpcmGlobalPolicyApplyService:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.ansible = AnsibleExecutor(settings)

    def apply(self, fields: dict[str, Any]) -> dict[str, Any]:
        updates = validate_global_policy_updates(fields)
        nodes = _managed_tpcm_nodes(self.session, self.settings)
        result: dict[str, Any] = {
            "ok": True,
            "fields": updates,
            "nodes_total": len(nodes),
            "nodes": [],
            "failed": [],
        }
        if not nodes:
            result["ok"] = False
            result["failed"].append({"node": "", "error": "no managed TPCM compute nodes"})
            return result

        for node, profile in nodes:
            try:
                node_result = self._apply_node(node, profile, updates)
            except Exception as exc:  # pragma: no cover - remote execution boundary
                node_result = {
                    "ok": False,
                    "node": node.hostname,
                    "error": str(exc),
                    "fields": updates,
                }
            result["nodes"].append(node_result)
            if not node_result.get("ok"):
                result["ok"] = False
                result["failed"].append(
                    {"node": node.hostname, "error": node_result.get("error") or "apply failed"}
                )
            self._record_node_audit(node.hostname, node_result)
        return result

    def _apply_node(
        self,
        node: ComputeNode,
        profile: Any,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        self.settings.temp_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"opentcsm-global-{node.hostname}-",
            dir=self.settings.temp_path,
        ) as temp_dir:
            workspace = Path(temp_dir)
            result_path = workspace / "opentcsm-global-policy-result.json"
            playbook_updates = []
            for field, value in updates.items():
                meta = GLOBAL_POLICY_FIELDS[field]
                auth_ref = opentcsm_auth_ref_for_profile(
                    profile,
                    str(meta["purpose"]),
                    self.settings,
                )
                auth = load_opentcsm_auth_material(self.settings, auth_ref)
                playbook_updates.append(
                    {
                        "field": field,
                        "label": meta["label"],
                        "policy_id": meta["id"],
                        "value": _playbook_value(value, str(meta["value_type"])),
                        "value_type": meta["value_type"],
                        "auth": auth.playbook_vars(),
                    }
                )
            ansible_result = self.ansible.run(
                playbook="apply-opentcsm-global-policy.yml",
                node=node,
                workspace=workspace,
                extra_vars={
                    "result_output_path": str(result_path),
                    "opentcsm_global_policy_updates": playbook_updates,
                },
            )
            if not result_path.is_file():
                raise RuntimeError(
                    "OpenTCSM global policy result file was not produced; "
                    f"rc={ansible_result.rc}; "
                    f"detail={(ansible_result.stderr or ansible_result.stdout)[-1600:]}"
                )
            data = json.loads(result_path.read_text(encoding="utf-8"))
            if ansible_result.rc != 0 or data.get("ok") is not True:
                raise RuntimeError(_format_apply_error(ansible_result, data))
            observed = (
                data.get("observed_after") if isinstance(data.get("observed_after"), dict) else {}
            )
            mismatches = _readback_mismatches(updates, observed)
            if mismatches:
                raise RuntimeError("TPCM global policy readback mismatch: " + ", ".join(mismatches))
            return {
                "ok": True,
                "node": node.hostname,
                "fields": updates,
                "observed_after": observed,
                "commands": data.get("commands") or [],
            }

    def _record_node_audit(self, hostname: str, result: dict[str, Any]) -> None:
        ok = bool(result.get("ok"))
        record_audit_event(
            self.session,
            event_type="tpcm_global_policy_apply",
            target=hostname,
            severity="info" if ok else "error",
            message="applied TPCM global policy" if ok else "failed to apply TPCM global policy",
            event_details={
                "log_type": "tpcm_global_policy",
                "subject_name": "TPCM",
                "object_name": hostname,
                "operation": "全局策略下发",
                "result": "成功" if ok else "失败",
                "fields": result.get("fields") or {},
                "error": result.get("error") or "",
            },
        )


def _managed_tpcm_nodes(session: Session, settings: Settings) -> list[tuple[ComputeNode, Any]]:
    rows = session.scalars(
        select(ComputeNode)
        .options(joinedload(ComputeNode.trust_profile))
        .order_by(ComputeNode.hostname)
    ).all()
    result = []
    for node in rows:
        profile = ensure_trusted_node_profile(session, node, settings)
        if (
            node.enabled
            and node.role == "compute"
            and profile.trust_managed
            and profile.trusted_root_type == TRUST_ROOT_TPCM
            and profile.adapter_type == ADAPTER_OPENTCSM
        ):
            result.append((node, profile))
    return result


def _latest_opentcsm_record(session: Session, node: ComputeNode) -> EvidenceRecord | None:
    return session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.node_id == node.id)
        .where(EvidenceRecord.provider == PROVIDER_OPENTCSM)
        .order_by(EvidenceRecord.collected_at.desc(), EvidenceRecord.id.desc())
        .limit(1)
    ).first()


def _latest_global_policy_request(session: Session) -> dict[str, Any] | None:
    event = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.event_type.in_(
                ("tpcm_global_policy_apply_queued", "tpcm_global_policy_apply")
            )
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    ).first()
    if not event:
        return None
    return {
        "event_id": event.id,
        "event_type": event.event_type,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "details": dict(event.event_details or {}),
    }


def _field_value_text(text: str, field: str) -> str | None:
    pattern = rf"(?:policy->|global_policy->|be_)?{re.escape(field)}\s*:\s*([^\r\n]+)"
    match = re.search(pattern, text or "", re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip().rstrip(";")


def _normalize_policy_value(raw: str, value_type: str) -> Any:
    token = str(raw or "").strip().split()[0].strip(",;")
    lowered = token.lower()
    if value_type == "bool":
        if lowered in {"on", "enable", "enabled", "true", "yes", "y", "1"}:
            return True
        if lowered in {"off", "disable", "disabled", "false", "no", "n", "0"}:
            return False
        try:
            return int(token, 0) != 0
        except ValueError:
            return None
    try:
        return int(token, 0)
    except ValueError:
        return None


def _coerce_update_value(field: str, value: Any, meta: dict[str, Any]) -> Any:
    if meta["value_type"] == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        lowered = str(value).strip().lower()
        if lowered in {"on", "enable", "enabled", "true", "yes", "1"}:
            return True
        if lowered in {"off", "disable", "disabled", "false", "no", "0"}:
            return False
        raise ValueError(f"TPCM global policy field {field} requires a boolean value")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"TPCM global policy field {field} requires an integer value") from exc
    minimum = meta.get("min")
    maximum = meta.get("max")
    if minimum is not None and number < int(minimum):
        raise ValueError(f"TPCM global policy field {field} must be >= {minimum}")
    if maximum is not None and number > int(maximum):
        raise ValueError(f"TPCM global policy field {field} must be <= {maximum}")
    return number


def _playbook_value(value: Any, value_type: str) -> int:
    if value_type == "bool":
        return 1 if value is True else 0
    return int(value)


def _readback_mismatches(updates: dict[str, Any], observed: dict[str, Any]) -> list[str]:
    mismatches: list[str] = []
    for field, expected in updates.items():
        item = observed.get(field)
        if not isinstance(item, dict):
            mismatches.append(f"{field}=missing expected {expected}")
            continue
        observed_value = item.get("value")
        if observed_value != expected:
            mismatches.append(f"{field}={observed_value!r} expected {expected!r}")
    return mismatches


def _format_apply_error(ansible_result: Any, data: dict[str, Any]) -> str:
    error = str(data.get("error") or "").strip()
    if not error:
        failed = [
            item
            for item in data.get("commands", [])
            if isinstance(item, dict) and int(item.get("rc") or 0) != 0
        ]
        error = "; ".join(f"{item.get('name')} rc={item.get('rc')}" for item in failed[:5])
    detail = error or ansible_result.stderr or ansible_result.stdout or "unknown error"
    return f"OpenTCSM global policy apply failed: {detail[-1600:]}"
