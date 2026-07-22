"""Evidence ingestion and OpenTCSM collection API routes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from keylime_openstack.api.deps import db_session, require_admin, settings_dep
from keylime_openstack.config import Settings
from keylime_openstack.constants import TRUST_AGENT_OPENTCSM_TPCM
from keylime_openstack.models import AuditEvent, ComputeNode
from keylime_openstack.schemas import HostIntegrityReportIn, OpenTcsmEvidenceReportIn
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.host_integrity import host_integrity_report_to_evidence
from keylime_openstack.services.opentcsm import opentcsm_report_to_evidence
from keylime_openstack.services.opentcsm_collect import OpenTcsmCollector
from keylime_openstack.services.trust_agents import node_trust_agent_type

router = APIRouter()


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
