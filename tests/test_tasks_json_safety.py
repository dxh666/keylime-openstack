from __future__ import annotations

from datetime import date, datetime, timezone

from keylime_openstack.models import TaskRun
from keylime_openstack.services.tasks import mark_failed, mark_success


def test_mark_success_stores_json_safe_result() -> None:
    task = TaskRun(task_type="sync", status="pending")
    now = datetime(2026, 7, 23, 13, 41, tzinfo=timezone.utc)

    mark_success(
        task,
        {
            "timestamp": now,
            "items": [{"date": date(2026, 7, 23), "tuple": ("a", now)}],
            "object": object(),
        },
    )

    assert task.result["timestamp"] == "2026-07-23T13:41:00+00:00"
    assert task.result["items"][0]["date"] == "2026-07-23"
    assert task.result["items"][0]["tuple"][1] == "2026-07-23T13:41:00+00:00"
    assert isinstance(task.result["object"], str)


def test_mark_failed_stores_json_safe_result() -> None:
    task = TaskRun(task_type="sync", status="running")

    mark_failed(task, "failed", {"seen": {1, 2}})

    assert sorted(task.result["seen"]) == [1, 2]
