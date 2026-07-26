import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from keylime_openstack.config import Settings
from keylime_openstack.database import Base
from keylime_openstack.models import AuditEvent, TaskRun
from keylime_openstack.services.tpcm_global_policy import (
    get_tpcm_global_policy_state,
    parse_global_control_policy,
    queue_tpcm_global_policy_apply,
    validate_global_policy_updates,
)


def test_global_control_policy_parser_extracts_known_fields() -> None:
    parsed = parse_global_control_policy(
        "policy->boot_measure_on: ON\n"
        "policy->boot_control: OFF\n"
        "policy->dynamic_measure_on: 1\n"
        "policy->dmeasure_max_busy_delay: 30\n"
    )

    assert parsed["boot_measure_on"]["value"] is True
    assert parsed["boot_control"]["value"] is False
    assert parsed["dynamic_measure_on"]["value"] is True
    assert parsed["dmeasure_max_busy_delay"]["value"] == 30


def test_global_policy_update_accepts_only_stage_one_writable_fields() -> None:
    assert validate_global_policy_updates(
        {
            "boot_measure_on": True,
            "boot_control": False,
            "dynamic_measure_on": True,
            "dmeasure_max_busy_delay": 30,
        }
    ) == {
        "boot_measure_on": True,
        "boot_control": False,
        "dynamic_measure_on": True,
        "dmeasure_max_busy_delay": 30,
    }

    with pytest.raises(ValueError, match="read-only"):
        validate_global_policy_updates({"program_control": True})


def test_global_policy_apply_is_queued_as_task() -> None:
    with _memory_session() as session:
        result = queue_tpcm_global_policy_apply(session, {"boot_measure_on": True})
        task = session.scalar(select(TaskRun).where(TaskRun.id == result["task_id"]))
        event = session.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "tpcm_global_policy_apply_queued")
        )

    assert result["ok"] is True
    assert task is not None
    assert task.task_type == "tpcm_global_policy_apply"
    assert task.task_args["fields"] == {"boot_measure_on": True}
    assert event is not None
    assert event.event_details["fields"] == {"boot_measure_on": True}


def test_global_policy_state_defaults_without_tpcm_nodes() -> None:
    with _memory_session() as session:
        result = get_tpcm_global_policy_state(session, Settings())

    assert result["ok"] is True
    assert result["nodes_total"] == 0
    assert result["fields"]["dynamic_measure_on"]["writable"] is True
    assert result["fields"]["program_control"]["writable"] is False


def _memory_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return Session(engine, future=True)
