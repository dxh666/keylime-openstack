"""OpenTCSM/TPCM dynamic measurement policy helpers."""

from __future__ import annotations

import re
from typing import Any

from keylime_openstack.constants import POLICY_DEPLOY_FAILED
from keylime_openstack.services.opentcsm_policy import OPEN_TCSM_DMEASURE_OBJECTS

__all__ = [
    "_dynamic_audit_object_name",
    "_dynamic_object_configs",
    "_dynamic_policy_context",
    "_looks_like_opentcsm_command_missing",
    "_looks_like_tpcm_auth_rejected",
    "_opentcsm_dynamic_apply_error",
    "_opentcsm_dynamic_failure_details",
    "_safe_int",
]

def _opentcsm_dynamic_apply_error(result: dict[str, Any]) -> str:
    failed = [
        item
        for item in result.get("commands", [])
        if isinstance(item, dict) and int(item.get("rc") or 0) != 0
    ]
    if not failed:
        return str(result.get("error") or "OpenTCSM 动态度量策略生效失败")
    details: list[str] = []
    for item in failed[:5]:
        command = item.get("command")
        command_text = " ".join(str(part) for part in command) if isinstance(command, list) else ""
        stdout = str(item.get("stdout") or "").strip()
        stderr = str(item.get("stderr") or "").strip()
        detail = f"{item.get('name') or 'command'} rc={item.get('rc')}"
        if command_text:
            detail += f"; command={command_text}"
        if stderr:
            detail += f"; stderr={stderr[-800:]}"
        if stdout:
            detail += f"; stdout={stdout[-800:]}"
        details.append(detail)
    return "OpenTCSM 动态度量策略生效失败：" + " | ".join(details)



def _opentcsm_dynamic_failure_details(error: str) -> dict[str, Any]:
    text = str(error or "")
    status = "unknown"
    code = "TPCM_DYNAMIC_APPLY_FAILED"
    summary = "TPCM 动态度量策略生效失败。"

    if "authorization material is not configured" in text:
        status = "missing"
        code = "TPCM_AUTH_MISSING"
        summary = "TPCM 策略生效授权材料未配置。"
    elif (
        "invalid OpenTCSM auth" in text
        or ("auth material" in text and "missing" in text)
        or "must be a hex string" in text
    ):
        status = "invalid"
        code = "TPCM_AUTH_INVALID"
        summary = "TPCM 策略生效授权材料格式无效。"
    elif _looks_like_tpcm_auth_rejected(text):
        status = "rejected"
        code = "TPCM_AUTH_REJECTED"
        summary = "TPCM 拒绝了策略生效授权，请检查 UID 和授权证书/密钥是否已在节点注册。"
    elif _looks_like_opentcsm_command_missing(text):
        code = "TPCM_COMMAND_NOT_FOUND"
        summary = (
            "OpenTCSM 动态度量命令不可用，请确认节点已完整安装 OpenTCSM，"
            "并提供 get_dmeasure_policy/update_dmeasure_policy 等工具。"
        )
    elif "校验未通过" in text or "validation" in text.lower():
        status = "normal"
        code = "TPCM_DYNAMIC_POLICY_NOT_CONSISTENT"
        summary = "TPCM 动态度量策略已执行，但节点当前状态与目标配置不一致。"
    elif "Ansible returned" in text or "OpenTCSM dynamic policy apply result" in text:
        status = "unknown"
        code = "TPCM_COMMAND_FAILED"
        summary = "OpenTCSM 策略生效命令执行异常。"

    return {
        "policy_apply_status": POLICY_DEPLOY_FAILED,
        "policy_apply_authorization_status": status,
        "policy_apply_error_code": code,
        "policy_apply_error_summary": summary,
    }



def _looks_like_tpcm_auth_rejected(text: str) -> bool:
    if re.search(r"\b(?:ret|rc)\s*[:=]\s*(?:0x0*98|152)\b", text, re.IGNORECASE):
        return True
    if "0x00000098" in text or "ret:0x98" in text or "ret: 0x98" in text:
        return True
    return False



def _looks_like_opentcsm_command_missing(text: str) -> bool:
    dynamic_commands = (
        "get_dmeasure_policy",
        "update_dmeasure_policy",
        "get_global_control_policy",
    )
    lowered = text.lower()
    for command in dynamic_commands:
        if (
            f"command not found: {command}" in lowered
            or f"no such file or directory: '{command}'" in lowered
            or f'no such file or directory: "{command}"' in lowered
            or f"{command} rc=127" in lowered
        ):
            return True
    return False



def _dynamic_audit_object_name(details: dict[str, Any]) -> str:
    if details.get("node_dynamic_measure_enabled") is False:
        return "全部动态度量对象"
    configs = details.get("environment_object_configs")
    if isinstance(configs, dict) and configs:
        enabled = [
            name
            for name, config in configs.items()
            if isinstance(config, dict) and config.get("enabled") is True
        ]
        return ",".join(enabled) if enabled else "none"
    objects = details.get("environment_objects")
    if isinstance(objects, list) and objects:
        return ",".join(str(item) for item in objects)
    return "环境动态度量策略"



def _dynamic_policy_context(content: dict[str, Any]) -> dict[str, Any]:
    object_configs = _dynamic_object_configs(content)
    node_dynamic_measure_enabled = bool(
        content.get(
            "node_dynamic_measure_enabled",
            content.get("dynamic_measure_required", True),
        )
    )
    environment_objects = [
        name
        for name, config in object_configs.items()
        if node_dynamic_measure_enabled and config["enabled"]
    ]
    return {
        "node_dynamic_measure_enabled": node_dynamic_measure_enabled,
        "environment_object_configs": object_configs,
        "environment_objects": environment_objects,
        "environment_interval_milli": int(content.get("environment_interval_milli") or 60000),
        "keylime_artifact": "opentcsm_dynamic_measurement_policy",
    }



def _dynamic_object_configs(content: dict[str, Any]) -> dict[str, dict[str, int | bool]]:
    default_interval = int(content.get("environment_interval_milli") or 60000)
    raw_configs = content.get("environment_object_configs")
    if isinstance(raw_configs, dict) and raw_configs:
        return {
            name: {
                "enabled": bool((raw_configs.get(name) or {}).get("enabled", False)),
                "interval_milli": int(
                    (raw_configs.get(name) or {}).get("interval_milli") or default_interval
                ),
            }
            for name in OPEN_TCSM_DMEASURE_OBJECTS
        }
    enabled_objects = set(content.get("environment_objects") or OPEN_TCSM_DMEASURE_OBJECTS)
    return {
        name: {
            "enabled": name in enabled_objects,
            "interval_milli": default_interval,
        }
        for name in OPEN_TCSM_DMEASURE_OBJECTS
    }



def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
