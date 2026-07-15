"""Small operational CLI wrapping the Python implementation."""

from __future__ import annotations

import argparse
import json

from keylime_openstack.database import SessionLocal
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.services.keylime_gate import keylime_only_check
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
    keylime_check.add_argument(
        "--count",
        type=int,
        default=1,
        help="Run the check this many times. Use with --interval for stability gates.",
    )
    keylime_check.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Seconds to wait between checks when --count is greater than 1.",
    )
    keylime_check.add_argument(
        "--failures-only",
        action="store_true",
        help="Only include untrusted nodes in the per-node output.",
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
        result = keylime_only_check(
            hosts=args.hosts,
            count=args.count,
            interval_seconds=args.interval,
            failures_only=args.failures_only,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if args.strict and not result["ok"]:
            raise SystemExit(2)
        return


if __name__ == "__main__":
    main()
