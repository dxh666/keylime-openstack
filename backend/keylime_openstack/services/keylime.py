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
        agent_ip: str = "",
        agent_port: int = 9002,
        tpm_policy: dict[str, Any] | None = None,
        runtime_policy: dict[str, Any] | None = None,
        runtime_policy_name: str = "",
        measured_boot_policy_name: str = "",
    ) -> dict[str, Any]:
        """Apply a policy using a temporary file only as a tool adapter."""

        Path(self.settings.temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            compose_args = [
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
                compose_args.extend(["-v", f"{tmp}:/keylime-openstack-tmp:ro"])
            tenant_args = ["-u", agent_uuid]
            if agent_ip:
                tenant_args.extend(["-t", agent_ip, "-tp", str(agent_port)])
            tenant_args.extend(self._tenant_service_endpoints())
            if tpm_policy is not None:
                tenant_args.extend(
                    ["--tpm_policy", json.dumps(tpm_policy, separators=(",", ":"))]
                )
            if runtime_policy_name:
                tenant_args.extend(["--runtime-policy-name", runtime_policy_name])
            if runtime_policy_path:
                tenant_args.extend(["--runtime-policy", runtime_policy_path])
            if measured_boot_policy_name:
                tenant_args.extend(["--mb-policy-name", measured_boot_policy_name])

            update = self._run_tenant_tool(
                [
                    *compose_args,
                    self.settings.keylime_tenant_service,
                    "-c",
                    "update",
                    *tenant_args,
                ]
            )
            applied = update
            operation = "update"
            if update["rc"] != 0 and agent_ip:
                applied = self._run_tenant_tool(
                    [
                        *compose_args,
                        self.settings.keylime_tenant_service,
                        "-c",
                        "add",
                        *tenant_args,
                    ]
                )
                operation = "add"
            if applied["rc"] != 0:
                return applied

            reactivate = self._run_tenant_tool(
                [
                    "docker",
                    "compose",
                    "run",
                    "--rm",
                    self.settings.keylime_tenant_service,
                    "-c",
                    "reactivate",
                    "-u",
                    agent_uuid,
                    *self._tenant_service_endpoints(),
                ]
            )
            if reactivate["rc"] != 0:
                return reactivate
            return {
                "rc": 0,
                "stdout": "\n".join(
                    part
                    for part in [
                        f"policy operation: {operation}",
                        applied.get("stdout", ""),
                        reactivate.get("stdout", ""),
                    ]
                    if part
                ),
                "stderr": "\n".join(
                    part
                    for part in [applied.get("stderr", ""), reactivate.get("stderr", "")]
                    if part
                ),
            }

    def tenant_tool_create_measured_boot_refstate(self, event_log: bytes) -> dict[str, Any]:
        """Create a Keylime measured boot reference state from a TPM event log."""

        Path(self.settings.temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            tmp_path = Path(tmp)
            tmp_path.chmod(0o777)
            event_path = tmp_path / "binary_bios_measurements"
            output_path = tmp_path / "measured-boot-refstate.json"
            event_path.write_bytes(event_log)
            output_path.touch(mode=0o666)
            command = [
                "docker",
                "compose",
                "run",
                "--rm",
                "-v",
                f"{tmp}:/keylime-openstack-tmp:rw",
                "--entrypoint",
                "create_mb_refstate",
                self.settings.keylime_tenant_service,
                "/keylime-openstack-tmp/binary_bios_measurements",
                "/keylime-openstack-tmp/measured-boot-refstate.json",
            ]
            result = self._run_tenant_tool(command)
            if result["rc"] != 0:
                raise RuntimeError(result["stderr"] or result["stdout"])
            return json.loads(output_path.read_text(encoding="utf-8"))

    def tenant_tool_create_runtime_policy(
        self,
        measurements: str,
        excludes: list[str],
    ) -> dict[str, Any]:
        """Generate a Keylime runtime policy from one node's IMA measurement list."""

        Path(self.settings.temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            tmp_path = Path(tmp)
            tmp_path.chmod(0o777)
            measurement_path = tmp_path / "ascii_runtime_measurements"
            excludes_path = tmp_path / "excludes.txt"
            output_path = tmp_path / "runtime-policy.json"
            measurement_path.write_text(measurements, encoding="utf-8")
            excludes_path.write_text("\n".join(excludes) + "\n", encoding="utf-8")
            output_path.touch(mode=0o666)
            command = [
                "docker",
                "compose",
                "run",
                "--rm",
                "-v",
                f"{tmp}:/keylime-openstack-tmp:rw",
                "--entrypoint",
                "keylime-policy",
                self.settings.keylime_tenant_service,
                "create",
                "runtime",
                "-m",
                "/keylime-openstack-tmp/ascii_runtime_measurements",
                "-e",
                "/keylime-openstack-tmp/excludes.txt",
                "-o",
                "/keylime-openstack-tmp/runtime-policy.json",
            ]
            result = self._run_tenant_tool(command)
            if result["rc"] != 0:
                raise RuntimeError(result["stderr"] or result["stdout"])
            return json.loads(output_path.read_text(encoding="utf-8"))

    def tenant_tool_store_measured_boot_policy(
        self,
        *,
        name: str,
        reference_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update a named policy in the Keylime verifier database."""

        Path(self.settings.temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            policy_path = Path(tmp) / "measured-boot-refstate.json"
            policy_path.write_text(json.dumps(reference_state), encoding="utf-8")
            base = [
                "docker",
                "compose",
                "run",
                "--rm",
                "-v",
                f"{tmp}:/keylime-openstack-tmp:ro",
                self.settings.keylime_tenant_service,
            ]
            policy_args = [
                "--mb-policy-name",
                name,
                "--mb-policy",
                "/keylime-openstack-tmp/measured-boot-refstate.json",
            ]
            update = self._run_tenant_tool([*base, "-c", "updatembpolicy", *policy_args])
            if update["rc"] == 0:
                return update
            create = self._run_tenant_tool([*base, "-c", "addmbpolicy", *policy_args])
            if create["rc"] != 0:
                detail = create["stderr"] or create["stdout"] or update["stderr"]
                raise RuntimeError(detail)
            return create

    def tenant_tool_store_runtime_policy(
        self,
        *,
        name: str,
        runtime_policy: dict[str, Any],
    ) -> dict[str, Any]:
        """Create or update one content-addressed runtime policy by name."""

        Path(self.settings.temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            policy_path = Path(tmp) / "runtime-policy.json"
            policy_path.write_text(json.dumps(runtime_policy), encoding="utf-8")
            base = [
                "docker",
                "compose",
                "run",
                "--rm",
                "-v",
                f"{tmp}:/keylime-openstack-tmp:ro",
                self.settings.keylime_tenant_service,
            ]
            policy_args = [
                "--runtime-policy-name",
                name,
                "--runtime-policy",
                "/keylime-openstack-tmp/runtime-policy.json",
            ]
            update = self._run_tenant_tool([*base, "-c", "updateruntimepolicy", *policy_args])
            if update["rc"] == 0:
                return update
            create = self._run_tenant_tool([*base, "-c", "addruntimepolicy", *policy_args])
            if create["rc"] != 0:
                detail = create["stderr"] or create["stdout"] or update["stderr"]
                raise RuntimeError(detail)
            return create

    def tenant_tool_delete_named_policy(self, *, policy_type: str, name: str) -> dict[str, Any]:
        command_name = {
            "measured_boot": "deletembpolicy",
            "ima_runtime": "deleteruntimepolicy",
        }.get(policy_type)
        option_name = {
            "measured_boot": "--mb-policy-name",
            "ima_runtime": "--runtime-policy-name",
        }.get(policy_type)
        if not command_name or not option_name:
            raise RuntimeError(f"named policy deletion is not implemented for {policy_type}")
        return self._run_tenant_tool(
            [
                "docker",
                "compose",
                "run",
                "--rm",
                self.settings.keylime_tenant_service,
                "-c",
                command_name,
                option_name,
                name,
            ]
        )

    def _verifier_agent_urls(self, agent_uuid: str) -> list[str]:
        base = self.settings.keylime_verifier_url.rstrip("/")
        urls = [f"{base}/v2.5/agents/{agent_uuid}"]
        parts = urlsplit(base)
        if parts.scheme == "http" and parts.port == 8881:
            https_base = urlunsplit(("https", parts.netloc, parts.path.rstrip("/"), "", ""))
            urls.append(f"{https_base}/v2.5/agents/{agent_uuid}")
        return urls

    def _tenant_service_endpoints(self) -> list[str]:
        verifier = urlsplit(self.settings.keylime_verifier_url)
        registrar = urlsplit(self.settings.keylime_registrar_url)
        args: list[str] = []
        if verifier.hostname:
            args.extend(["-v", verifier.hostname])
            if verifier.port:
                args.extend(["-vp", str(verifier.port)])
        if registrar.hostname:
            args.extend(["-r", registrar.hostname])
            if registrar.port:
                args.extend(["-rp", str(registrar.port)])
        return args

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
