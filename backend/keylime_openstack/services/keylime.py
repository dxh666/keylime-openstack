"""Keylime verifier/registrar API client and tenant-tool fallback."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from keylime_openstack.config import Settings


class KeylimeClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def verifier_agent_status(self, agent_uuid: str) -> dict[str, Any]:
        """Read verifier state from Keylime API.

        Keylime deployments differ by version and TLS configuration. The API
        endpoint used here is intentionally narrow and can be overridden later by
        a version-aware adapter without changing the decision engine.
        """

        url = f"{self.settings.keylime_verifier_url.rstrip('/')}/v2.5/agents/{agent_uuid}"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    def tenant_tool_status(self, agent_uuid: str) -> dict[str, Any]:
        command = [
            "docker",
            "compose",
            "run",
            "--rm",
            self.settings.keylime_tenant_service,
            "-c",
            "status",
            "-u",
            agent_uuid,
        ]
        return self._run_tenant_tool(command)

    def tenant_tool_apply_policy(
        self,
        *,
        agent_uuid: str,
        tpm_policy: dict[str, Any] | None = None,
        runtime_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a policy using a temporary file only as a tool adapter."""

        Path(self.settings.temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            args = [
                "docker",
                "compose",
                "run",
                "--rm",
            ]
            runtime_policy_path = ""
            if runtime_policy is not None:
                policy_path = Path(tmp) / "runtime-policy.json"
                policy_path.write_text(json.dumps(runtime_policy, indent=2) + "\n", encoding="utf-8")
                runtime_policy_path = "/keylime-openstack-tmp/runtime-policy.json"
                args.extend(["-v", f"{tmp}:/keylime-openstack-tmp:ro"])
            args.extend([self.settings.keylime_tenant_service, "-c", "update", "-u", agent_uuid])
            if tpm_policy is not None:
                args.extend(["--tpm_policy", json.dumps(tpm_policy, separators=(",", ":"))])
            if runtime_policy_path:
                args.extend(["--runtime-policy", runtime_policy_path])
            return self._run_tenant_tool(args)

    def _run_tenant_tool(self, command: list[str]) -> dict[str, Any]:
        if not self.settings.keylime_tenant_tool_enabled:
            return {"rc": 1, "stdout": "", "stderr": "tenant tool fallback disabled"}
        completed = subprocess.run(
            command,
            cwd=self.settings.keylime_docker_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=240,
        )
        return {
            "rc": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
