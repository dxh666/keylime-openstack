"""Worker process for periodic trust synchronization."""

from __future__ import annotations

import logging
import signal
import time

from sqlalchemy import select

from keylime_openstack.config import get_settings
from keylime_openstack.constants import TASK_PENDING
from keylime_openstack.database import SessionLocal
from keylime_openstack.models import TaskRun
from keylime_openstack.services.policy_deployment import PolicyDeploymentService
from keylime_openstack.services.sync import TrustSyncService
from keylime_openstack.services.tasks import create_task, mark_failed, mark_running, mark_success

LOG = logging.getLogger("keylime_openstack.worker")


class Worker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while self.running:
            self.run_once()
            sleep_for = max(self.settings.worker_interval_seconds, 5)
            for _ in range(sleep_for):
                if not self.running:
                    break
                time.sleep(1)

    def run_once(self) -> dict[str, object]:
        with SessionLocal() as session:
            policy_result = self._run_next_policy_deployment(session)
            task = create_task(session, "sync", requested_by="worker")
            mark_running(task)
            try:
                result = TrustSyncService(session, self.settings).run_once()
            except Exception as exc:  # pragma: no cover - production safety boundary
                LOG.exception("trust sync failed")
                mark_failed(task, str(exc))
                session.commit()
                return {"ok": False, "error": str(exc), "policy_deployment": policy_result}
            mark_success(task, result)
            session.commit()
            return {"ok": True, "result": result, "policy_deployment": policy_result}

    def _run_next_policy_deployment(self, session) -> dict[str, object] | None:
        task = session.scalars(
            select(TaskRun)
            .where(TaskRun.task_type == "policy_deploy")
            .where(TaskRun.status == TASK_PENDING)
            .order_by(TaskRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if not task:
            return None
        mark_running(task)
        session.commit()
        try:
            policy_id = int(task.task_args["policy_id"])
            result = PolicyDeploymentService(session, self.settings).deploy(policy_id)
        except Exception as exc:  # pragma: no cover - remote execution boundary
            LOG.exception("policy deployment failed")
            mark_failed(task, str(exc))
            session.commit()
            return {"ok": False, "task_id": task.id, "error": str(exc)}
        if result.get("failed"):
            mark_failed(task, "one or more node deployments failed", result)
        else:
            mark_success(task, result)
        session.commit()
        return {"ok": not bool(result.get("failed")), "task_id": task.id, "result": result}


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    Worker().run_forever()


if __name__ == "__main__":
    run()
