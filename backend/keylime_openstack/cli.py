"""Small operational CLI wrapping the Python implementation."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import select

from keylime_openstack.config import get_settings
from keylime_openstack.database import SessionLocal
from keylime_openstack.models import ComputeNode
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.keylime import KeylimeClient
from keylime_openstack.services.sync import keylime_status_to_evidence
from keylime_openstack.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate the Keylime OpenStack trust plane.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap", help="Create default hardware profiles and lab nodes.")
    sub.add_parser("sync", help="Run one trust synchronization cycle.")
    keylime_check = sub.add_parser(
        "keylime-check",
        help="Check Keylime-only attestation state without OpenStack enforcement.",
    )
    keylime_check.add_argument(
        "--hosts",
        default="",
        help="Comma-separated compute host filter. Defaults to all enabled compute nodes.",
    )
    keylime_check.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when any selected node is not boot/runtime trusted.",
    )
    args = parser.parse_args()

    if args.command == "bootstrap":
        with SessionLocal() as session:
            ensure_default_environment(session)
            session.commit()
        print(json.dumps({"ok": True, "command": "bootstrap"}))
        return

    if args.command == "sync":
        result = Worker().run_once()
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return

    if args.command == "keylime-check":
        result = keylime_only_check(hosts=args.hosts)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if args.strict and not result["ok"]:
            raise SystemExit(2)
        return


def keylime_only_check(hosts: str = "") -> dict[str, object]:
    settings = get_settings()
    wanted = {item.strip() for item in hosts.split(",") if item.strip()}
    with SessionLocal() as session:
        ensure_default_environment(session)
        session.flush()
        query = (
            select(ComputeNode)
            .where(ComputeNode.role == "compute")
            .where(ComputeNode.enabled.is_(True))
            .order_by(ComputeNode.hostname)
        )
        nodes = list(session.scalars(query).all())
        if wanted:
            nodes = [node for node in nodes if node.hostname in wanted]

        client = KeylimeClient(settings)
        results = []
        all_ok = bool(nodes)
        for node in nodes:
            item = _keylime_node_check(client, node, settings)
            results.append(item)
            all_ok = all_ok and bool(item.get("trusted"))
        session.commit()

    return {
        "ok": all_ok,
        "mode": "keylime-only",
        "trust_policy_mode": settings.normalized_trust_policy_mode,
        "openstack_enforcement_enabled": settings.openstack_enforcement_enabled,
        "nodes_total": len(results),
        "nodes_trusted": sum(1 for item in results if item.get("trusted")),
        "nodes": results,
    }


def _keylime_node_check(
    client: KeylimeClient,
    node: ComputeNode,
    settings,
) -> dict[str, object]:
    base: dict[str, object] = {
        "host": node.hostname,
        "agent_uuid": node.keylime_agent_uuid,
        "agent_ip": node.keylime_agent_ip,
    }
    if not node.keylime_agent_uuid:
        return {
            **base,
            "trusted": False,
            "status": "skipped",
            "reason": "missing-agent-uuid",
        }

    try:
        status = client.read_agent_status(node.keylime_agent_uuid)
    except Exception as exc:
        return {
            **base,
            "trusted": False,
            "status": "error",
            "reason": str(exc),
        }

    records = keylime_status_to_evidence(node, status, settings)
    evidence = {record.evidence_type: record.status for record in records}
    boot_ok = evidence.get("boot") == "pass"
    runtime_ok = evidence.get("runtime") == "pass"
    trusted = boot_ok and runtime_ok
    return {
        **base,
        "trusted": trusted,
        "status": "collected",
        "source": status.get("_source") or "unknown",
        "attestation_status": status.get("attestation_status"),
        "operational_state": status.get("operational_state"),
        "last_event_id": status.get("last_event_id"),
        "has_runtime_policy": _truthy(status.get("has_runtime_policy")),
        "last_received_quote": status.get("last_received_quote"),
        "last_successful_attestation": status.get("last_successful_attestation"),
        "tpm_policy": _json_or_value(status.get("tpm_policy")),
        "evidence": evidence,
    }


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "enabled", "pass"}


def _json_or_value(value: object) -> object:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


if __name__ == "__main__":
    main()
