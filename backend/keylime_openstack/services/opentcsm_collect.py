"""Active OpenTCSM/Hygon TPCM evidence collection."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    ADAPTER_OPENTCSM,
    CAPABILITY_EVM,
    CAPABILITY_IMA_RUNTIME,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    REGISTRATION_CONFLICT,
    REGISTRATION_REGISTERED,
    REGISTRATION_UNTRUSTED,
    REGISTRATION_VERIFIED,
    TRUST_AGENT_OPENTCSM_TPCM,
    TRUST_ROOT_TPCM,
)
from keylime_openstack.models import ComputeNode
from keylime_openstack.services.ansible import AnsibleExecutor, AnsibleResult
from keylime_openstack.services.audit import record_audit_event
from keylime_openstack.services.opentcsm import opentcsm_report_to_evidence
from keylime_openstack.services.opentcsm_policy import parse_dmeasure_policy
from keylime_openstack.services.trust_agents import node_trust_agent_type
from keylime_openstack.services.trust_registration import (
    ensure_trusted_node_profile,
    upsert_trusted_node_profile,
)


NONZERO_TRUST_REPORT_FIELDS = (
    "be_ilegal_program_load",
    "be_ilegal_lib_load",
    "be_ilegal_kernel_module_load",
    "be_ilegal_file_access",
    "be_ilegal_device_access",
    "be_ilegal_network_inreq",
    "be_ilegal_network_outreq",
    "be_process_code_measure_fail",
    "be_kernel_code_measure_fail",
    "be_kernel_data_measure_fail",
    "be_notify_fail",
)


class OpenTcsmCollector:
    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings
        self.ansible = AnsibleExecutor(settings)

    def collect(self, node: ComputeNode) -> dict[str, Any]:
        agent_type = node_trust_agent_type(node, self.settings)
        if agent_type != TRUST_AGENT_OPENTCSM_TPCM:
            raise RuntimeError(f"node {node.hostname} does not use OpenTCSM/TPCM")

        self.settings.temp_path.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f"opentcsm-{node.hostname}-",
            dir=self.settings.temp_path,
        ) as temp_dir:
            workspace = Path(temp_dir)
            evidence_path = workspace / "opentcsm-evidence.json"
            result = self.ansible.run(
                playbook="collect-opentcsm-evidence.yml",
                node=node,
                workspace=workspace,
                extra_vars={"evidence_output_path": str(evidence_path)},
            )
            if result.rc != 0:
                raise RuntimeError(_format_ansible_error(result))
            if not evidence_path.is_file():
                raise RuntimeError("OpenTCSM evidence file was not produced")
            collected = json.loads(evidence_path.read_text(encoding="utf-8"))

        report = normalize_opentcsm_collection(node, collected)
        records = opentcsm_report_to_evidence(node, report, self.settings)
        for record in records:
            self.session.add(record)
        self.session.flush()
        sync_tpcm_profile(self.session, self.settings, node, report)
        dynamic_objects = [
            str(item.get("object"))
            for item in report["raw"].get("dmeasure_policy", [])
            if isinstance(item, dict) and item.get("object")
        ]
        dmeasure_policy_sha256 = report["raw"].get("dmeasure_policy_sha256") or ""
        trust_report_sha256 = report["raw"].get("trust_report_sha256") or ""
        record_audit_event(
            self.session,
            event_type="opentcsm_evidence_collect",
            target=node.hostname,
            severity="info" if all(record.status == "pass" for record in records) else "warning",
            message="collected OpenTCSM/Hygon TPCM evidence from node",
            event_details={
                "provider": "opentcsm",
                "source": "ansible",
                "statuses": {record.evidence_type: record.status for record in records},
                "evidence_ids": [record.id for record in records],
                "tpcm_id": report["raw"].get("tpcm_id", ""),
                "report_hash": trust_report_sha256,
                "log_type": "dynamic_measurement",
                "subject_name": "TPCM",
                "object_name": ",".join(dynamic_objects) if dynamic_objects else "-",
                "measurement_type": "状态采集",
                "measurement_baseline": dmeasure_policy_sha256 or trust_report_sha256,
                "operation": "状态刷新",
                "result": "成功" if report.get("dynamic_measurement_status") == "pass" else "异常",
                "hash": dmeasure_policy_sha256 or trust_report_sha256,
            },
        )
        return {
            "ok": True,
            "node": node.hostname,
            "provider": "opentcsm",
            "communication": "ok",
            "trust_root": report["trust_root"],
            "agent_name": report["agent_name"],
            "trusted": report["trusted"],
            "boot_status": report["boot_status"],
            "dynamic_measurement_status": report["dynamic_measurement_status"],
            "summary": report["summary"],
            "raw": report["raw"],
            "records": [
                {
                    "evidence_id": record.id,
                    "evidence_type": record.evidence_type,
                    "status": record.status,
                    "summary": record.summary,
                }
                for record in records
            ],
        }


def sync_tpcm_profile(
    session: Session,
    settings: Settings,
    node: ComputeNode,
    report: dict[str, Any],
) -> None:
    profile = ensure_trusted_node_profile(session, node, settings)
    raw = report.get("raw") if isinstance(report.get("raw"), dict) else {}
    if profile.trust_managed and profile.adapter_type and profile.adapter_type != ADAPTER_OPENTCSM:
        profile.registration_status = REGISTRATION_CONFLICT
        profile.last_evidence_summary = {
            **dict(profile.last_evidence_summary or {}),
            "trusted": False,
            "reason": "conflicting-tpcm-evidence",
            "source": "opentcsm",
        }
        return
    tpcm_id = str(raw.get("tpcm_id") or "")
    if not profile.trust_managed or profile.adapter_type != ADAPTER_OPENTCSM:
        profile = upsert_trusted_node_profile(
            session,
            node,
            settings,
            {
                "hostname": node.hostname,
                "openstack_compute_name": node.hypervisor_name or node.hostname,
                "management_ip": node.management_ip,
                "is_openstack_compute": node.role == "compute",
                "trust_managed": True,
                "trusted_root_type": TRUST_ROOT_TPCM,
                "adapter_type": ADAPTER_OPENTCSM,
                "agent_endpoint": {"transport": "ssh", "host": node.management_ip},
                "agent_identity": {
                    "tpcm_id": tpcm_id,
                    "registration_source": "opentcsm-evidence",
                },
                "capabilities": {
                    CAPABILITY_TRUSTED_BOOT: True,
                    CAPABILITY_IMA_RUNTIME: False,
                    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: True,
                    CAPABILITY_EVM: False,
                },
            },
        )
    identity = dict(profile.agent_identity or {})
    if tpcm_id:
        identity["tpcm_id"] = tpcm_id
    boot_records = raw.get("boot_records") if isinstance(raw.get("boot_records"), list) else []
    dmeasure_policy = raw.get("dmeasure_policy") if isinstance(raw.get("dmeasure_policy"), list) else []
    boot_reference_count = raw.get("boot_measure_ref_number")
    profile.agent_identity = identity
    profile.last_verified_at = datetime.now(timezone.utc)
    profile.last_evidence_summary = {
        "trusted": report.get("trusted"),
        "evidence": {
            "boot": report.get("boot_status") or "unknown",
            "runtime": report.get("dynamic_measurement_status") or "unknown",
        },
        "boot_measurement_summary": {
            "type": "tpcm_boot_measurement",
            "enabled": raw.get("boot_measure_on"),
            "status": report.get("boot_status") or "unknown",
            "record_count": len(boot_records),
            "reference_count": boot_reference_count,
            "records_preview": boot_records[:5],
            "baseline_ready": bool(boot_reference_count),
            "records_sha256": raw.get("boot_measure_records_sha256") or "",
            "trust_report_sha256": raw.get("trust_report_sha256") or "",
        },
        "dynamic_measurement_summary": {
            "type": "tpcm_dynamic_measurement",
            "enabled": raw.get("dynamic_measure_on"),
            "status": report.get("dynamic_measurement_status") or "unknown",
            "object_count": len(dmeasure_policy),
            "dmeasure_times": raw.get("dmeasure_times"),
            "policy_sha256": raw.get("dmeasure_policy_sha256") or "",
        },
        "source": "opentcsm",
    }
    if report.get("trusted") is True:
        profile.registration_status = REGISTRATION_VERIFIED
    elif report.get("trusted") is False:
        profile.registration_status = REGISTRATION_UNTRUSTED
    else:
        profile.registration_status = REGISTRATION_REGISTERED


def normalize_opentcsm_collection(node: ComputeNode, collected: dict[str, Any]) -> dict[str, Any]:
    commands = collected.get("commands") or {}
    trust_status = _stdout(commands, "trust_status")
    trust_report = _stdout(commands, "trust_report")
    global_policy = _stdout(commands, "global_control_policy")
    dmeasure_policy_text = _stdout(commands, "dmeasure_policy")
    boot_records_text = _stdout(commands, "boot_measure_records")
    tpcm_info = _stdout(commands, "tpcm_info")
    tpcm_id_text = _stdout(commands, "tpcm_id")

    trusted_status = bool(re.search(r"Trust status:\s*trusted\b", trust_status, re.IGNORECASE))
    untrusted_status = bool(re.search(r"Trust status:\s*(untrusted|fail|failed)\b", trust_status, re.IGNORECASE))
    boot_on = _policy_on(trust_report, "be_boot_measure_on") or _policy_on(global_policy, "boot_measure_on")
    dynamic_on = _policy_on(trust_report, "be_dynamic_measure_on") or _policy_on(
        global_policy, "dynamic_measure_on"
    )
    trust_report_clean = _trust_report_failures(trust_report) == {}
    boot_records = _boot_record_names(boot_records_text)

    boot_status = _status_from_signals(
        trusted=trusted_status,
        untrusted=untrusted_status,
        enabled=boot_on or bool(boot_records),
        clean=trust_report_clean,
    )
    dynamic_status = _status_from_signals(
        trusted=trusted_status,
        untrusted=untrusted_status,
        enabled=dynamic_on,
        clean=trust_report_clean,
    )

    tpcm_id = _tpcm_id(tpcm_id_text) or _tpcm_id(tpcm_info)
    raw = {
        "hostname": collected.get("hostname") or node.hostname,
        "management_ip": node.management_ip,
        "tpcm_id": tpcm_id,
        "trust_status": "trusted" if trusted_status else "untrusted" if untrusted_status else "unknown",
        "boot_measure_on": boot_on,
        "boot_status": boot_status,
        "dynamic_measure_on": dynamic_on,
        "dynamic_measurement_status": dynamic_status,
        "trust_report_clean": trust_report_clean,
        "trust_report_eval": _trust_report_eval(trust_report),
        "trust_report_failures": _trust_report_failures(trust_report),
        "boot_records": boot_records,
        "boot_measure_records_sha256": _sha256(boot_records_text),
        "trust_report_sha256": _sha256(trust_report),
        "policy_report_sha256": _sha256(_stdout(commands, "policy_report")),
        "global_control_policy_sha256": _sha256(global_policy),
        "dmeasure_policy": parse_dmeasure_policy(dmeasure_policy_text),
        "dmeasure_policy_sha256": _sha256(dmeasure_policy_text),
        "dmeasure_process_policy_sha256": _sha256(_stdout(commands, "dmeasure_process_policy")),
        "admin_cert_list_sha256": _sha256(_stdout(commands, "admin_cert_list")),
        "dmeasure_times": _first_int(tpcm_info, "dmeasure_times"),
        "boot_measure_ref_number": _first_int(tpcm_info, "boot_measure_ref_number"),
        "dynamic_measure_ref_number": _first_int(tpcm_info, "dynamic_measure_ref_number"),
        "commands": commands,
    }
    return {
        "hostname": node.hostname,
        "collected_at": collected.get("collected_at"),
        "trust_root": collected.get("trust_root") or "Hygon TPCM",
        "agent_name": collected.get("agent_name") or "OpenTCSM",
        "agent_version": collected.get("agent_version") or "",
        "report_type": collected.get("report_type") or "tpcm",
        "boot_status": boot_status,
        "dynamic_measurement_status": dynamic_status,
        "trusted": boot_status == "pass" and dynamic_status == "pass",
        "summary": _summary(boot_status, dynamic_status, raw),
        "raw": raw,
        "errors": _command_errors(commands),
    }


def _stdout(commands: dict[str, Any], name: str) -> str:
    item = commands.get(name) or {}
    return str(item.get("stdout") or "")


def _policy_on(text: str, field: str) -> bool:
    return bool(re.search(rf"\b{re.escape(field)}:\s*ON\b", text, re.IGNORECASE))


def _trust_report_failures(text: str) -> dict[str, str]:
    failures: dict[str, str] = {}
    for field in NONZERO_TRUST_REPORT_FIELDS:
        match = re.search(rf"\b{re.escape(field)}:\s*(0x[0-9a-fA-F]+|\d+)", text)
        if match and int(match.group(1), 0) != 0:
            failures[field] = match.group(1)
    return failures


def _trust_report_eval(text: str) -> int | None:
    match = re.search(r"\bbe_eval:\s*(0x[0-9a-fA-F]+|\d+)", text)
    return int(match.group(1), 0) if match else None


def _status_from_signals(*, trusted: bool, untrusted: bool, enabled: bool, clean: bool) -> str:
    if untrusted:
        return "fail"
    if trusted and enabled and clean:
        return "pass"
    if trusted and clean:
        return "unknown"
    return "fail"


def _boot_record_names(text: str) -> list[str]:
    return [
        match.group(1).strip()
        for match in re.finditer(r"^\[\d+\]\.name:\s*(.+)$", text, re.MULTILINE)
        if match.group(1).strip()
    ]


def _tpcm_id(text: str) -> str:
    parts = [
        match.group(1)
        for match in re.finditer(r"\s([0-9A-F]{16})\s*$", text, re.MULTILINE)
    ]
    return "".join(parts[:2]) if len(parts) >= 2 else ""


def _first_int(text: str, field: str) -> int | None:
    match = re.search(rf"\b{re.escape(field)}:\s*(0x[0-9a-fA-F]+|\d+)", text)
    return int(match.group(1), 0) if match else None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def _command_errors(commands: dict[str, Any]) -> list[str]:
    errors = []
    for name, item in commands.items():
        rc = int(item.get("rc") or 0)
        stderr = str(item.get("stderr") or "").strip()
        if rc != 0:
            errors.append(f"{name} rc={rc}: {stderr}")
    return errors


def _summary(boot_status: str, dynamic_status: str, raw: dict[str, Any]) -> str:
    if boot_status == "pass" and dynamic_status == "pass":
        return "OpenTCSM TPCM report trusted"
    if raw["trust_report_failures"]:
        return "OpenTCSM TPCM report contains non-zero failure counters"
    return "OpenTCSM TPCM report incomplete"


def _format_ansible_error(result: AnsibleResult) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    if len(detail) > 4000:
        detail = detail[-4000:]
    return f"OpenTCSM evidence collection failed rc={result.rc}; detail={detail}"
