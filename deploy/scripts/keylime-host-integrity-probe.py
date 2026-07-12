#!/usr/bin/env python3
"""Collect host IMA appraisal, EVM, keyring, and xattr evidence."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.request
from collections import Counter, deque
from pathlib import Path
from typing import Any


DEFAULT_XATTR_PATHS = (
    "/usr/bin/runc",
    "/usr/bin/containerd",
    "/usr/bin/dockerd",
    "/usr/bin/qemu-system-x86_64",
    "/usr/bin/virsh",
    "/usr/bin/python3",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hostname", default=socket.gethostname())
    parser.add_argument("--api-url", default=os.environ.get("KEYLIME_OPENSTACK_API_URL", ""))
    parser.add_argument("--admin-token", default=os.environ.get("KEYLIME_OPENSTACK_ADMIN_TOKEN", ""))
    parser.add_argument("--xattr-path", action="append", default=[])
    args = parser.parse_args()

    report = collect_report(args.hostname, tuple(args.xattr_path or DEFAULT_XATTR_PATHS))
    print(json.dumps(report, indent=2, sort_keys=True))

    if args.api_url:
        response = post_report(args.api_url, args.admin_token, args.hostname, report)
        print(json.dumps(response, indent=2, sort_keys=True), file=sys.stderr)
    return 0


def collect_report(hostname: str, xattr_paths: tuple[str, ...]) -> dict[str, Any]:
    policy, policy_error = read_lines("/sys/kernel/security/ima/policy")
    templates, measurement_tail, measurement_error = read_ima_measurements()
    dmesg = run_command(["dmesg"])
    dmesg_lines = [
        line
        for line in dmesg["stdout"].splitlines()
        if any(item in line.lower() for item in ("ima", "evm", "appraisal", "x509", "keyring"))
    ][-120:]

    errors = []
    if policy_error:
        errors.append(f"ima_policy: {policy_error}")
    if measurement_error:
        errors.append(f"ima_measurements: {measurement_error}")
    if dmesg["rc"] != 0:
        errors.append(f"dmesg: {dmesg['stderr'] or dmesg['stdout']}")

    return {
        "hostname": hostname,
        "cmdline": read_text("/proc/cmdline"),
        "ima_policy": policy,
        "ima_policy_error": policy_error,
        "ima_keyring": keyring_report("%:.ima"),
        "evm_keyring": keyring_report("%:.evm"),
        "dmesg_integrity_tail": dmesg_lines,
        "securityfs_mounted": securityfs_mounted(),
        "ima_measurement_templates": dict(templates),
        "ima_measurement_tail": measurement_tail,
        "xattrs": xattr_report(xattr_paths),
        "errors": errors,
        "facts": {
            "kernel": run_command(["uname", "-a"])["stdout"],
        },
    }


def post_report(api_url: str, token: str, hostname: str, report: dict[str, Any]) -> dict[str, Any]:
    url = f"{api_url.rstrip('/')}/api/nodes/{hostname}/host-integrity"
    request = urllib.request.Request(
        url,
        data=json.dumps(report).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Admin-Token": token,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def read_lines(path: str) -> tuple[list[str], str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").splitlines(), ""
    except OSError as exc:
        return [], str(exc)


def read_ima_measurements() -> tuple[Counter[str], list[str], str]:
    templates: Counter[str] = Counter()
    tail: deque[str] = deque(maxlen=80)
    path = Path("/sys/kernel/security/ima/ascii_runtime_measurements")
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.rstrip("\n")
                tail.append(stripped)
                parts = stripped.split(maxsplit=3)
                if len(parts) >= 3:
                    templates[parts[2]] += 1
        return templates, list(tail), ""
    except OSError as exc:
        return templates, list(tail), str(exc)


def keyring_report(name: str) -> dict[str, Any]:
    result = run_command(["keyctl", "list", name])
    combined = "\n".join([result["stdout"], result["stderr"]])
    lines = [line for line in combined.splitlines() if line.strip()]
    key_lines = [line for line in lines if "keyring is empty" not in line.lower()]
    return {
        "name": name,
        "rc": result["rc"],
        "key_count": len(key_lines) if result["rc"] == 0 else 0,
        "lines": lines,
        "stderr": result["stderr"],
    }


def xattr_report(paths: tuple[str, ...]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for path in paths:
        item: dict[str, str] = {"exists": str(Path(path).exists()).lower()}
        if Path(path).exists():
            for attr in ("security.ima", "security.evm"):
                try:
                    value = os.getxattr(path, attr)
                except OSError as exc:
                    item[attr] = f"missing:{exc.errno}"
                else:
                    item[attr] = f"present:{len(value)}:{value[:16].hex()}"
        result[path] = item
    return result


def securityfs_mounted() -> bool:
    mounts = read_text("/proc/mounts")
    return any(
        line.split()[1:3] == ["/sys/kernel/security", "securityfs"]
        for line in mounts.splitlines()
    )


def run_command(command: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        return {"rc": 127, "stdout": "", "stderr": str(exc)}
    except subprocess.TimeoutExpired as exc:
        return {"rc": 124, "stdout": exc.stdout or "", "stderr": str(exc)}
    return {
        "rc": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
