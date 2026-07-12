"""Small operational CLI wrapping the Python implementation."""

from __future__ import annotations

import argparse
import json

from keylime_openstack.database import SessionLocal
from keylime_openstack.seed import ensure_default_environment
from keylime_openstack.worker import Worker


def main() -> None:
    parser = argparse.ArgumentParser(description="Operate the Keylime OpenStack trust plane.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap", help="Create default hardware profiles and lab nodes.")
    sub.add_parser("sync", help="Run one trust synchronization cycle.")
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


if __name__ == "__main__":
    main()
