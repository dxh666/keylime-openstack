from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import keylime_openstack.services.tpcm_boot_deployment as tpcm_boot_deployment
from keylime_openstack.config import Settings
from keylime_openstack.constants import (
    BINDING_NODE,
    CAPABILITY_TPCM_DYNAMIC_MEASUREMENT,
    CAPABILITY_TRUSTED_BOOT,
    POLICY_DEPLOY_APPLIED,
    POLICY_MEASURED_BOOT,
    POLICY_TPCM_DYNAMIC_MEASUREMENT,
    TRUST_ROOT_TPCM,
)
from keylime_openstack.database import Base
from keylime_openstack.models import (
    AuditEvent,
    ComputeNode,
    EvidenceRecord,
    PolicyBinding,
    TrustPolicy,
    TrustedNodeProfile,
)
from keylime_openstack.services.tpcm_boot_deployment import (
    TpcmBootDeployment,
    _tpcm_boot_evidence_failures,
)
from keylime_openstack.services.trust_capabilities import build_trust_capability_summary


def test_tpcm_boot_evidence_failures_accept_clean_boot_report() -> None:
    failures = _tpcm_boot_evidence_failures(_tpcm_policy_content(), _clean_tpcm_raw())

    assert failures == []


def test_tpcm_boot_evidence_failures_require_boot_records() -> None:
    raw = {**_clean_tpcm_raw(), "boot_records": []}

    failures = _tpcm_boot_evidence_failures(_tpcm_policy_content(), raw)

    assert "TPCM boot measurement records are missing" in failures


def test_tpcm_boot_evidence_failures_detect_expected_hash_change() -> None:
    content = {
        **_tpcm_policy_content(),
        "expected_boot_record_count": 2,
        "expected_boot_records_sha256": "old-records-sha256",
        "expected_trust_report_sha256": "old-trust-report-sha256",
    }

    failures = _tpcm_boot_evidence_failures(content, _clean_tpcm_raw())

    assert "TPCM boot measurement records hash changed" in failures
    assert "TPCM trust report hash changed" in failures


def test_tpcm_boot_deployment_binds_management_baseline(monkeypatch, tmp_path: Path) -> None:
    class FakeCollector:
        def __init__(self, *_: object) -> None:
            pass

        def collect(self, _: ComputeNode) -> dict[str, object]:
            return {"trusted": True, "raw": _clean_tpcm_raw()}

    @contextmanager
    def workspace():
        yield tmp_path

    monkeypatch.setattr(tpcm_boot_deployment, "OpenTcsmCollector", FakeCollector)
    deployment = TpcmBootDeployment(
        session=object(),  # type: ignore[arg-type]
        settings=Settings(),
        workspace_factory=workspace,
    )
    policy = TrustPolicy(
        name="hygon23-boot",
        policy_type=POLICY_MEASURED_BOOT,
        content=_tpcm_policy_content(),
    )
    node = ComputeNode(hostname="hygon23", management_ip="172.31.100.23")

    result = deployment.deploy(policy, node)

    assert result.external_name.startswith("klos-tpcm-boot-hygon23-boot-hygon23-")
    assert result.deployment_details["keylime_artifact"] == "opentcsm_tpcm_boot_policy"
    assert result.deployment_details["trusted_root_type"] == TRUST_ROOT_TPCM
    assert result.deployment_details["baseline_status"] == "management_baseline_bound"
    assert result.deployment_details["tpcm_write_status"] == "not_enabled"
    assert result.rendered_policy["type"] == "tpcm_trusted_boot_baseline"
    assert result.rendered_policy["expected"]["boot_record_count"] == 2
    assert result.rendered_policy["expected"]["boot_reference_count"] == 2
    assert result.response["boot_status"] == "pass"
    assert result.response["rendered_policy_sha256"]


def test_trust_capability_summary_uses_tpcm_boot_policy_binding() -> None:
    with _memory_session() as session:
        node = ComputeNode(
            hostname="hygon23",
            hypervisor_name="hygon23",
            management_ip="172.31.100.23",
            role="compute",
        )
        profile = TrustedNodeProfile(
            node=node,
            hostname="hygon23",
            openstack_compute_name="hygon23",
            management_ip="172.31.100.23",
            is_openstack_compute=True,
            trust_managed=True,
            trusted_root_type=TRUST_ROOT_TPCM,
            capabilities={
                CAPABILITY_TRUSTED_BOOT: True,
                CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: True,
            },
        )
        tpm_policy = TrustPolicy(
            name="wrong-tpm-boot",
            policy_type=POLICY_MEASURED_BOOT,
            status="active",
            content={"trusted_root_type": "tpm"},
        )
        tpcm_policy = TrustPolicy(
            name="hygon23-boot",
            policy_type=POLICY_MEASURED_BOOT,
            status="active",
            content={"trusted_root_type": TRUST_ROOT_TPCM},
        )
        session.add_all([node, profile, tpm_policy, tpcm_policy])
        session.flush()
        session.add_all(
            [
                PolicyBinding(
                    policy=tpm_policy,
                    target_type=BINDING_NODE,
                    target_id=node.id,
                    active=True,
                    application_status=POLICY_DEPLOY_APPLIED,
                    binding_details={"keylime_artifact": "measured_boot_refstate"},
                ),
                PolicyBinding(
                    policy=tpcm_policy,
                    target_type=BINDING_NODE,
                    target_id=node.id,
                    active=True,
                    application_status=POLICY_DEPLOY_APPLIED,
                    binding_details={
                        "keylime_artifact": "opentcsm_tpcm_boot_policy",
                        "baseline_status": "management_baseline_bound",
                        "rendered_policy_sha256": "rendered-sha256",
                        "boot_measure_ref_number": 2,
                        "tpcm_write_status": "not_enabled",
                    },
                ),
            ]
        )
        now = datetime.now(timezone.utc)
        evidence = EvidenceRecord(
            node_id=node.id,
            provider="opentcsm",
            evidence_type="boot",
            collected_at=now,
            valid_until=now + timedelta(minutes=5),
            status="pass",
            summary="TPCM trusted boot passed",
            payload={},
        )
        session.add(evidence)
        session.flush()

        summary = build_trust_capability_summary(session, node, profile, [evidence])

    trusted_boot = summary[CAPABILITY_TRUSTED_BOOT]
    assert trusted_boot["supported"] is True
    assert trusted_boot["policy_bound"] is True
    assert trusted_boot["status"] == "pass"
    assert trusted_boot["binding_id"]
    assert trusted_boot["baseline"]["artifact"] == "opentcsm_tpcm_boot_policy"
    assert trusted_boot["baseline"]["content_sha256"] == "rendered-sha256"
    assert trusted_boot["baseline"]["boot_reference_count"] == 2
    assert trusted_boot["baseline"]["tpcm_write_status"] == "not_enabled"


def test_trust_capability_summary_requires_policy_before_boot_pass() -> None:
    with _memory_session() as session:
        node = ComputeNode(
            hostname="hygon23",
            hypervisor_name="hygon23",
            management_ip="172.31.100.23",
            role="compute",
        )
        profile = TrustedNodeProfile(
            node=node,
            hostname="hygon23",
            trust_managed=True,
            trusted_root_type=TRUST_ROOT_TPCM,
            capabilities={
                CAPABILITY_TRUSTED_BOOT: True,
                CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: True,
            },
        )
        session.add_all([node, profile])
        session.flush()
        now = datetime.now(timezone.utc)
        evidence = EvidenceRecord(
            node_id=node.id,
            provider="opentcsm",
            evidence_type="boot",
            collected_at=now,
            valid_until=now + timedelta(minutes=5),
            status="pass",
            summary="TPCM trusted boot passed",
            payload={},
        )
        session.add(evidence)
        session.flush()

        summary = build_trust_capability_summary(session, node, profile, [evidence])

    trusted_boot = summary[CAPABILITY_TRUSTED_BOOT]
    assert trusted_boot["policy_bound"] is False
    assert trusted_boot["status"] == "unconfigured"
    assert trusted_boot["effective"] is False


def test_trust_capability_summary_honors_tpcm_dynamic_global_switch() -> None:
    with _memory_session() as session:
        node = ComputeNode(
            hostname="hygon23",
            hypervisor_name="hygon23",
            management_ip="172.31.100.23",
            role="compute",
        )
        profile = TrustedNodeProfile(
            node=node,
            hostname="hygon23",
            trust_managed=True,
            trusted_root_type=TRUST_ROOT_TPCM,
            capabilities={
                CAPABILITY_TRUSTED_BOOT: True,
                CAPABILITY_TPCM_DYNAMIC_MEASUREMENT: True,
            },
        )
        policy = TrustPolicy(
            name="hygon23-dynamic",
            policy_type=POLICY_TPCM_DYNAMIC_MEASUREMENT,
            status="active",
            content={
                "node_dynamic_measure_enabled": True,
                "dynamic_measure_required": True,
            },
        )
        session.add_all([node, profile, policy])
        session.flush()
        session.add(
            PolicyBinding(
                policy=policy,
                target_type=BINDING_NODE,
                target_id=node.id,
                active=True,
                application_status=POLICY_DEPLOY_APPLIED,
            )
        )
        now = datetime.now(timezone.utc)
        evidence = EvidenceRecord(
            node_id=node.id,
            provider="opentcsm",
            evidence_type="runtime",
            collected_at=now,
            valid_until=now + timedelta(minutes=5),
            status="pass",
            summary="TPCM dynamic measurement passed",
            payload={},
        )
        session.add(
            AuditEvent(
                event_type="tpcm_dynamic_global_switch",
                event_details={"global_dynamic_measure_enabled": False},
            )
        )
        session.add(evidence)
        session.flush()

        summary = build_trust_capability_summary(session, node, profile, [evidence])

    dynamic = summary[CAPABILITY_TPCM_DYNAMIC_MEASUREMENT]
    assert dynamic["policy_bound"] is True
    assert dynamic["policy_enabled"] is False
    assert dynamic["status"] == "disabled"
    assert dynamic["effective"] is False


def _tpcm_policy_content() -> dict[str, object]:
    return {
        "trusted_root_type": TRUST_ROOT_TPCM,
        "boot_measure_required": True,
        "minimum_boot_references": 1,
        "require_clean_trust_report": True,
        "require_trust_status": "trusted",
        "require_trust_report_eval": 100,
        "tpcm_write_enabled": False,
        "auth_material_ref": "bmeasure-uid",
    }


def _clean_tpcm_raw() -> dict[str, object]:
    return {
        "tpcm_id": "tpcm-hygon23",
        "boot_measure_on": True,
        "boot_status": "pass",
        "trust_status": "trusted",
        "trust_report_clean": True,
        "trust_report_eval": 100,
        "boot_measure_ref_number": 2,
        "boot_records": ["BIOS/U-BOOT", "shim.efi"],
        "boot_references": ["BIOS/U-BOOT", "shim.efi"],
        "boot_measure_records_sha256": "records-sha256",
        "boot_measure_references_sha256": "refs-sha256",
        "trust_report_sha256": "trust-report-sha256",
        "policy_report_sha256": "policy-report-sha256",
        "global_control_policy_sha256": "global-policy-sha256",
    }


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine, future=True)
