from __future__ import annotations

from datetime import datetime, timedelta, timezone

from keylime_openstack.models import EvidenceRecord
from keylime_openstack.services.decision import evaluate_trust, unmanaged_trust_decision


class DummySettings:
    def __init__(
        self,
        *,
        boot: bool = True,
        ima: bool = True,
        evm: bool = False,
        openstack_service: bool = False,
    ) -> None:
        self.legacy_trait_enabled = True
        self.normalized_trust_policy_mode = "ima-only"
        self.effective_trust_capabilities = {
            "boot": boot,
            "ima": ima,
            "evm": evm,
            "openstack_service": openstack_service,
        }


def _evidence(evidence_type: str, status: str) -> EvidenceRecord:
    now = datetime.now(timezone.utc)
    return EvidenceRecord(
        id={"boot": 1, "runtime": 2, "evm": 3}.get(evidence_type, 9),
        node_id=1,
        provider="keylime",
        evidence_type=evidence_type,
        collected_at=now,
        valid_until=now + timedelta(minutes=5),
        status=status,
        summary="test evidence",
        payload={},
    )


def test_boot_only_capability_can_trust_node_with_runtime_failure() -> None:
    result = evaluate_trust(
        evidence=[_evidence("boot", "pass"), _evidence("runtime", "fail")],
        openstack_state=None,
        settings=DummySettings(boot=True, ima=False),
    )

    assert result["trusted"] is True
    assert result["boot_trusted"] is True
    assert result["runtime_trusted"] is False
    assert result["reason"] == "TRUSTED"
    assert result["details"]["enabled_trust_capabilities"] == ["boot"]


def test_ima_only_capability_can_trust_node_with_boot_failure() -> None:
    result = evaluate_trust(
        evidence=[_evidence("boot", "fail"), _evidence("runtime", "pass")],
        openstack_state=None,
        settings=DummySettings(boot=False, ima=True),
    )

    assert result["trusted"] is True
    assert result["boot_trusted"] is False
    assert result["runtime_trusted"] is True
    assert result["reason"] == "TRUSTED"
    assert result["details"]["enabled_trust_capabilities"] == ["ima"]


def test_enabled_evm_capability_blocks_trust_until_evm_passes() -> None:
    result = evaluate_trust(
        evidence=[_evidence("boot", "pass"), _evidence("runtime", "pass")],
        openstack_state=None,
        settings=DummySettings(boot=True, ima=True, evm=True),
    )

    assert result["trusted"] is False
    assert result["reason"] == "WAITING_FOR_EVM"
    assert result["details"]["trust_capabilities"]["evm"] is True


def test_no_enabled_capability_never_trusts_node() -> None:
    result = evaluate_trust(
        evidence=[_evidence("boot", "pass"), _evidence("runtime", "pass")],
        openstack_state=None,
        settings=DummySettings(boot=False, ima=False, evm=False),
    )

    assert result["trusted"] is False
    assert result["reason"] == "NO_KEYLIME_TRUST_CAPABILITY_ENABLED"


def test_openstack_service_gate_cannot_replace_keylime_trust() -> None:
    result = evaluate_trust(
        evidence=[_evidence("boot", "pass"), _evidence("runtime", "pass")],
        openstack_state=None,
        settings=DummySettings(
            boot=False,
            ima=False,
            evm=False,
            openstack_service=True,
        ),
    )

    assert result["trusted"] is False
    assert result["reason"] == "NO_KEYLIME_TRUST_CAPABILITY_ENABLED"


def test_unmanaged_node_has_product_decision_reason() -> None:
    result = unmanaged_trust_decision(openstack_state=None)

    assert result["trusted"] is False
    assert result["reason"] == "TRUST_AGENT_UNMANAGED"
    assert result["desired_traits"] == []
    assert result["details"]["trust_managed"] is False
    assert result["details"]["trust_management_status"] == "unmanaged"
