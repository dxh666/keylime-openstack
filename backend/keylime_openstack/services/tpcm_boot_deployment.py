"""TPCM trusted boot policy baseline deployment.

The first production path binds a management-plane baseline from live
OpenTCSM/Hygon TPCM evidence. Native TPCM boot reference writes are represented
as an explicit status field and remain opt-in for a later stage once boot
authorization material is configured and verified.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.constants import TRUST_AGENT_OPENTCSM_TPCM, TRUST_ROOT_TPCM
from keylime_openstack.models import ComputeNode, TrustPolicy
from keylime_openstack.services.opentcsm_collect import OpenTcsmCollector
from keylime_openstack.services.opentcsm_policy import opentcsm_auth_ref_for_profile
from keylime_openstack.services.policy_artifacts import _content_hash, _external_name
from keylime_openstack.services.trust_registration import ensure_trusted_node_profile


@dataclass(frozen=True)
class TpcmBootDeploymentResult:
    external_name: str
    rendered_policy: dict[str, Any]
    deployment_details: dict[str, Any]
    response: dict[str, Any]


class TpcmBootDeployment:
    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        workspace_factory: Callable[[], AbstractContextManager[Path]],
    ) -> None:
        self.session = session
        self.settings = settings
        self.workspace_factory = workspace_factory

    def deploy(self, policy: TrustPolicy, node: ComputeNode) -> TpcmBootDeploymentResult:
        profile = ensure_trusted_node_profile(self.session, node, self.settings)
        auth_ref = opentcsm_auth_ref_for_profile(
            profile,
            "boot",
            self.settings,
            policy.content,
        )
        result = OpenTcsmCollector(self.session, self.settings).collect(node)
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        failures = _tpcm_boot_evidence_failures(policy.content, raw)
        if failures:
            raise RuntimeError("TPCM trusted boot baseline cannot be bound: " + "; ".join(failures))

        rendered_policy = _tpcm_boot_baseline(policy.content, node, raw, auth_ref=auth_ref)
        external_name = _external_name("tpcm-boot", policy.name, node.hostname, rendered_policy)
        rendered_sha256 = _content_hash(rendered_policy)
        records_sha256 = str(raw.get("boot_measure_records_sha256") or "")
        trust_report_sha256 = str(raw.get("trust_report_sha256") or "")
        write_status = _tpcm_write_status(policy.content, auth_ref=auth_ref)
        deployment_details = {
            "keylime_artifact": "opentcsm_tpcm_boot_policy",
            "adapter_artifact": "opentcsm_tpcm_boot_policy",
            "trust_agent_type": TRUST_AGENT_OPENTCSM_TPCM,
            "trusted_root_type": TRUST_ROOT_TPCM,
            "evidence_type": "tpcm_boot_measurement",
            "evidence_sha256": records_sha256 or trust_report_sha256,
            "rendered_policy_sha256": rendered_sha256,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "deployed_by": "keylime-openstack-management-baseline",
            "baseline_status": "management_baseline_bound",
            "tpcm_write_status": write_status["status"],
            "tpcm_write_enabled": write_status["enabled"],
            "tpcm_auth_ref": write_status["auth_ref"],
            "boot_measure_on": raw.get("boot_measure_on"),
            "boot_status": raw.get("boot_status") or "",
            "trust_status": raw.get("trust_status") or "",
            "trust_report_clean": raw.get("trust_report_clean"),
            "trust_report_eval": raw.get("trust_report_eval"),
            "boot_measure_ref_number": raw.get("boot_measure_ref_number"),
            "boot_record_count": len(_boot_records(raw)),
            "boot_measure_records_sha256": records_sha256,
            "boot_measure_references_sha256": raw.get("boot_measure_references_sha256") or "",
            "trust_report_sha256": trust_report_sha256,
        }
        response = {
            "keylime_policy_name": "",
            "external_policy_name": external_name,
            "keylime_artifact": "opentcsm_tpcm_boot_policy",
            "baseline_status": "management_baseline_bound",
            "tpcm_write_status": write_status["status"],
            "auth_ref": write_status["auth_ref"],
            "tpcm_auth_ref": write_status["auth_ref"],
            "trusted_root_type": TRUST_ROOT_TPCM,
            "boot_measure_on": raw.get("boot_measure_on"),
            "boot_status": raw.get("boot_status") or "",
            "trust_status": raw.get("trust_status") or "",
            "boot_record_count": len(_boot_records(raw)),
            "boot_measure_ref_number": raw.get("boot_measure_ref_number"),
            "boot_measure_records_sha256": records_sha256,
            "rendered_policy_sha256": rendered_sha256,
        }
        return TpcmBootDeploymentResult(
            external_name=external_name,
            rendered_policy=rendered_policy,
            deployment_details=deployment_details,
            response=response,
        )


def _tpcm_boot_baseline(
    content: dict[str, Any],
    node: ComputeNode,
    raw: dict[str, Any],
    *,
    auth_ref: str = "",
) -> dict[str, Any]:
    boot_records = _boot_records(raw)
    boot_references = _boot_references(raw)
    return {
        "type": "tpcm_trusted_boot_baseline",
        "version": 1,
        "node": node.hostname,
        "trusted_root_type": TRUST_ROOT_TPCM,
        "trust_agent_type": TRUST_AGENT_OPENTCSM_TPCM,
        "tpcm_id": str(raw.get("tpcm_id") or ""),
        "requirements": {
            "boot_measure_required": bool(content.get("boot_measure_required", True)),
            "minimum_boot_references": int(content.get("minimum_boot_references") or 0),
            "require_clean_trust_report": bool(content.get("require_clean_trust_report", True)),
            "require_trust_status": str(content.get("require_trust_status") or "trusted"),
            "require_trust_report_eval": int(content.get("require_trust_report_eval") or 100),
        },
        "expected": {
            "boot_measure_on": raw.get("boot_measure_on"),
            "boot_status": raw.get("boot_status") or "",
            "trust_status": raw.get("trust_status") or "",
            "trust_report_clean": raw.get("trust_report_clean"),
            "trust_report_eval": raw.get("trust_report_eval"),
            "boot_record_count": len(boot_records),
            "boot_records": boot_records,
            "boot_measure_records_sha256": raw.get("boot_measure_records_sha256") or "",
            "boot_reference_count": raw.get("boot_measure_ref_number"),
            "boot_references_preview": boot_references[:5],
            "boot_measure_references_sha256": raw.get("boot_measure_references_sha256") or "",
            "trust_report_sha256": raw.get("trust_report_sha256") or "",
            "policy_report_sha256": raw.get("policy_report_sha256") or "",
            "global_control_policy_sha256": raw.get("global_control_policy_sha256") or "",
        },
        "apply": {
            "mode": str(content.get("tpcm_apply_mode") or "management_baseline"),
            "tpcm_write_enabled": bool(content.get("tpcm_write_enabled", False)),
            "auth_material_ref": str(auth_ref or content.get("auth_material_ref") or ""),
            "operation": str(content.get("tpcm_operation") or "add"),
            "stage": content.get("tpcm_stage"),
        },
    }


def _tpcm_boot_evidence_failures(content: dict[str, Any], raw: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    boot_records = _boot_records(raw)
    reference_count = _safe_int(raw.get("boot_measure_ref_number"))
    minimum_refs = _safe_int(content.get("minimum_boot_references")) or 0
    expected_record_count = _safe_int(content.get("expected_boot_record_count"))
    expected_records_sha256 = str(content.get("expected_boot_records_sha256") or "").strip()
    expected_report_sha256 = str(content.get("expected_trust_report_sha256") or "").strip()
    required_status = str(content.get("require_trust_status") or "trusted").lower()
    required_eval = _safe_int(content.get("require_trust_report_eval"))
    if not str(raw.get("tpcm_id") or "").strip():
        failures.append("TPCM ID is missing")
    if bool(content.get("boot_measure_required", True)) and raw.get("boot_measure_on") is not True:
        failures.append("TPCM boot measurement is not enabled")
    if str(raw.get("boot_status") or "").lower() != "pass":
        failures.append(f"TPCM boot status is {raw.get('boot_status') or 'unknown'}")
    if required_status and str(raw.get("trust_status") or "").lower() != required_status:
        failures.append(f"TPCM trust status is {raw.get('trust_status') or 'unknown'}")
    if bool(content.get("require_clean_trust_report", True)) and raw.get("trust_report_clean") is not True:
        failures.append("TPCM trust report contains failure counters")
    if required_eval is not None and raw.get("trust_report_eval") != required_eval:
        failures.append(
            f"TPCM trust report eval is {raw.get('trust_report_eval') or 'unknown'}"
        )
    if reference_count is None or reference_count < minimum_refs:
        failures.append(
            f"TPCM boot references are below policy minimum ({reference_count or 0} < {minimum_refs})"
        )
    if not boot_records:
        failures.append("TPCM boot measurement records are missing")
    if expected_record_count is not None and len(boot_records) != expected_record_count:
        failures.append(
            f"TPCM boot record count changed ({len(boot_records)} != {expected_record_count})"
        )
    if (
        expected_records_sha256
        and str(raw.get("boot_measure_records_sha256") or "") != expected_records_sha256
    ):
        failures.append("TPCM boot measurement records hash changed")
    if expected_report_sha256 and str(raw.get("trust_report_sha256") or "") != expected_report_sha256:
        failures.append("TPCM trust report hash changed")
    return failures


def _tpcm_write_status(content: dict[str, Any], *, auth_ref: str = "") -> dict[str, Any]:
    enabled = bool(content.get("tpcm_write_enabled", False))
    auth_ref = str(auth_ref or content.get("auth_material_ref") or "").strip()
    if not enabled:
        return {"enabled": False, "status": "not_enabled", "auth_ref": auth_ref}
    if not auth_ref:
        return {"enabled": True, "status": "authorization_missing", "auth_ref": ""}
    return {"enabled": True, "status": "reserved_not_executed", "auth_ref": auth_ref}


def _boot_records(raw: dict[str, Any]) -> list[str]:
    return raw.get("boot_records") if isinstance(raw.get("boot_records"), list) else []


def _boot_references(raw: dict[str, Any]) -> list[str]:
    return (
        raw.get("boot_references")
        if isinstance(raw.get("boot_references"), list)
        else []
    )


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
