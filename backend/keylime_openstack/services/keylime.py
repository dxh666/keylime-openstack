"""Keylime verifier/registrar API client and tenant-tool fallback."""

from __future__ import annotations

import json
import ssl
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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

        errors: list[str] = []
        for url in self._verifier_agent_urls(agent_uuid):
            try:
                async with httpx.AsyncClient(**self._http_client_kwargs(url)) as client:
                    response = await client.get(url)
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:  # pragma: no cover - deployment-specific API boundary
                errors.append(f"{url}: {_exception_summary(exc)}")
        raise RuntimeError("; ".join(errors))

    def verifier_agent_status_sync(self, agent_uuid: str) -> dict[str, Any]:
        errors: list[str] = []
        for url in self._verifier_agent_urls(agent_uuid):
            try:
                with httpx.Client(**self._http_client_kwargs(url)) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:  # pragma: no cover - deployment-specific API boundary
                errors.append(f"{url}: {_exception_summary(exc)}")
        raise RuntimeError("; ".join(errors))

    def read_agent_status(self, agent_uuid: str) -> dict[str, Any]:
        """Read and normalize one agent status from Keylime.

        Direct verifier API access is preferred because the long-running worker
        should not depend on host shell tooling. The tenant container remains a
        fallback for older or mTLS-only deployments.
        """

        errors: list[str] = []
        try:
            status = normalize_agent_payload(
                agent_uuid,
                self.verifier_agent_status_sync(agent_uuid),
            )
            status["_source"] = "verifier-api"
            return status
        except Exception as exc:  # pragma: no cover - deployment-specific API boundary
            errors.append(f"verifier-api: {exc}")

        result = self.tenant_tool_status(agent_uuid)
        if result.get("rc") != 0:
            stderr = str(result.get("stderr") or "").strip()
            stdout = str(result.get("stdout") or "").strip()
            detail = stderr or stdout or "tenant tool returned non-zero status"
            raise RuntimeError("; ".join([*errors, f"tenant-tool: {detail}"]))

        try:
            status = parse_tenant_status_stdout(agent_uuid, str(result.get("stdout") or ""))
        except ValueError as exc:
            raise RuntimeError("; ".join([*errors, f"tenant-tool: {exc}"])) from exc
        status["_source"] = "tenant-tool"
        if errors:
            status["_adapter_errors"] = errors
        return status

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
                policy_path.write_text(
                    json.dumps(runtime_policy, indent=2) + "\n",
                    encoding="utf-8",
                )
                runtime_policy_path = "/keylime-openstack-tmp/runtime-policy.json"
                args.extend(["-v", f"{tmp}:/keylime-openstack-tmp:ro"])
            args.extend([self.settings.keylime_tenant_service, "-c", "update", "-u", agent_uuid])
            if tpm_policy is not None:
                args.extend(["--tpm_policy", json.dumps(tpm_policy, separators=(",", ":"))])
            if runtime_policy_path:
                args.extend(["--runtime-policy", runtime_policy_path])
            return self._run_tenant_tool(args)

    def _verifier_agent_urls(self, agent_uuid: str) -> list[str]:
        base = self.settings.keylime_verifier_url.rstrip("/")
        urls = [f"{base}/v2.5/agents/{agent_uuid}"]
        parts = urlsplit(base)
        if parts.scheme == "http" and parts.port == 8881:
            https_base = urlunsplit(("https", parts.netloc, parts.path.rstrip("/"), "", ""))
            urls.append(f"{https_base}/v2.5/agents/{agent_uuid}")
        return urls

    def _http_client_kwargs(self, url: str) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": self.settings.keylime_api_timeout_seconds}
        if not url.lower().startswith("https://"):
            return kwargs

        kwargs["verify"] = self.settings.keylime_tls_verify
        if self.settings.keylime_tls_verify:
            kwargs["verify"] = self._tls_verify_config()

        if self.settings.keylime_tls_client_cert and self.settings.keylime_tls_client_key:
            kwargs["cert"] = (
                self.settings.keylime_tls_client_cert,
                self.settings.keylime_tls_client_key,
            )
        elif self.settings.keylime_tls_client_cert:
            kwargs["cert"] = self.settings.keylime_tls_client_cert
        return kwargs

    def _tls_verify_config(self) -> str | ssl.SSLContext | bool:
        if self.settings.keylime_tls_verify_hostname:
            return self.settings.keylime_tls_ca_cert or True

        context = ssl.create_default_context(
            cafile=self.settings.keylime_tls_ca_cert or None,
        )
        context.check_hostname = False
        return context

    def _run_tenant_tool(self, command: list[str]) -> dict[str, Any]:
        if not self.settings.keylime_tenant_tool_enabled:
            return {"rc": 1, "stdout": "", "stderr": "tenant tool fallback disabled"}
        try:
            completed = subprocess.run(
                command,
                cwd=self.settings.keylime_docker_dir,
                check=False,
                capture_output=True,
                text=True,
                timeout=240,
            )
        except FileNotFoundError as exc:
            return {"rc": 127, "stdout": "", "stderr": _exception_summary(exc)}
        except subprocess.TimeoutExpired as exc:
            return {"rc": 124, "stdout": exc.stdout or "", "stderr": _exception_summary(exc)}
        return {
            "rc": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }


def normalize_agent_payload(agent_uuid: str, payload: Any) -> dict[str, Any]:
    """Return the verifier-status dict for an agent from common Keylime shapes."""

    if not isinstance(payload, dict):
        raise ValueError(f"unexpected Keylime payload type: {type(payload).__name__}")

    candidate: Any = payload.get("results", payload)
    if isinstance(candidate, dict) and agent_uuid in candidate:
        candidate = candidate[agent_uuid]
    elif isinstance(candidate, dict) and isinstance(candidate.get("agents"), dict):
        agents = candidate["agents"]
        if agent_uuid in agents:
            candidate = agents[agent_uuid]

    if isinstance(candidate, dict) and "attestation_status" in candidate:
        return dict(candidate)

    if isinstance(candidate, dict) and "operational_state" in candidate:
        return dict(candidate)

    raise ValueError(f"agent {agent_uuid} verifier status not found in Keylime payload")


def parse_tenant_status_stdout(agent_uuid: str, stdout: str) -> dict[str, Any]:
    """Extract verifier status JSON from keylime-tenant command output."""

    candidates: list[dict[str, Any]] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            candidates.append(normalize_agent_payload(agent_uuid, payload))
        except ValueError:
            continue

    for candidate in candidates:
        if "attestation_status" in candidate:
            return candidate
    if candidates:
        return candidates[-1]
    raise ValueError(f"no JSON status for agent {agent_uuid} in tenant output")


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return f"{exc.__class__.__module__}.{exc.__class__.__name__}"
