"""Keylime verifier/registrar API client and tenant-tool fallback."""

from __future__ import annotations

import json
import re
import shutil
import ssl
import subprocess
import tempfile
from ast import literal_eval
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from keylime_openstack.config import Settings


UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


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

    def verifier_delete_agent_sync(self, agent_uuid: str) -> dict[str, Any]:
        """Delete an agent enrollment from the Keylime verifier only."""

        errors: list[str] = []
        for url in self._verifier_agent_urls(agent_uuid):
            try:
                with httpx.Client(**self._http_client_kwargs(url)) as client:
                    response = client.delete(url)
                    if response.status_code == 404:
                        return {
                            "rc": 0,
                            "stdout": f"verifier enrollment already absent: {agent_uuid}",
                            "stderr": "",
                            "command": ["DELETE", url],
                        }
                    response.raise_for_status()
                    return {
                        "rc": 0,
                        "stdout": f"deleted verifier enrollment: {agent_uuid}",
                        "stderr": "",
                        "command": ["DELETE", url],
                    }
            except Exception as exc:  # pragma: no cover - deployment-specific API boundary
                errors.append(f"{url}: {_exception_summary(exc)}")
        return {
            "rc": 1,
            "stdout": "",
            "stderr": "; ".join(errors),
            "command": ["DELETE", self._verifier_agent_urls(agent_uuid)[0]],
        }

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

    def list_registered_agents(self, *, use_tenant_tool: bool = False) -> list[dict[str, Any]]:
        """Discover Keylime agents from verifier/registrar APIs.

        Keylime deployments differ in both version and JSON envelope. This method
        keeps those differences inside the adapter and returns a narrow inventory
        shape for product-level registration code.
        """

        errors: list[str] = []
        agents: list[dict[str, Any]] = []
        successful_reads = 0
        for url, source in self._agent_list_urls():
            try:
                with httpx.Client(**self._http_client_kwargs(url)) as client:
                    response = client.get(url)
                    response.raise_for_status()
                    successful_reads += 1
                    agents.extend(normalize_agent_inventory_payload(response.json(), source=source))
            except Exception as exc:  # pragma: no cover - deployment-specific API boundary
                errors.append(f"{source} {url}: {_exception_summary(exc)}")

        if agents:
            return _dedupe_agent_inventory(agents)

        if use_tenant_tool:
            result = self.tenant_tool_reglist()
            if result.get("rc") == 0:
                agents.extend(parse_tenant_reglist_stdout(str(result.get("stdout") or "")))
                if agents:
                    return _dedupe_agent_inventory(agents)
            errors.append(f"tenant-tool-reglist: {result.get('stderr') or result.get('stdout')}")

        if successful_reads:
            return []
        if errors:
            raise RuntimeError("; ".join(errors))
        return []

    def tenant_tool_reglist(self) -> dict[str, Any]:
        command = [
            "docker",
            "compose",
            "run",
            "--rm",
            self.settings.keylime_tenant_service,
            "-c",
            "reglist",
            *self._tenant_service_endpoints(),
        ]
        return self._run_tenant_tool(command)

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
        disable_measured_boot: bool = False,
        replace_existing: bool = False,
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
            mount_tmp = False
            runtime_policy_path = ""
            if runtime_policy is not None:
                policy_path = Path(tmp) / "runtime-policy.json"
                policy_path.write_text(
                    json.dumps(runtime_policy, indent=2) + "\n",
                    encoding="utf-8",
                )
                runtime_policy_path = "/keylime-openstack-tmp/runtime-policy.json"
                mount_tmp = True
            if disable_measured_boot:
                compose_args.extend(["-e", "KEYLIME_TENANT_MB_REFSTATE="])
            if mount_tmp:
                compose_args.extend(["-v", f"{tmp}:/keylime-openstack-tmp:ro"])
            tenant_args = ["-u", agent_uuid]
            if agent_ip:
                tenant_args.extend(["-t", agent_ip, "-tp", str(agent_port)])
            tenant_args.extend(self._tenant_service_endpoints())
            if tpm_policy is not None:
                tenant_tpm_policy = dict(tpm_policy)
                tenant_tpm_policy.pop("mask", None)
                tenant_args.extend(
                    ["--tpm_policy", json.dumps(tenant_tpm_policy, separators=(",", ":"))]
                )
            if runtime_policy_name:
                tenant_args.extend(["--runtime-policy-name", runtime_policy_name])
            if runtime_policy_path:
                tenant_args.extend(["--runtime-policy", runtime_policy_path])
            if measured_boot_policy_name and not disable_measured_boot:
                tenant_args.extend(["--mb-policy-name", measured_boot_policy_name])

            delete = {"rc": 0, "stdout": "", "stderr": ""}
            if replace_existing:
                delete = self.verifier_delete_agent_sync(agent_uuid)
                if delete["rc"] != 0:
                    return delete
                applied = self._run_tenant_tool(
                    [
                        *compose_args,
                        self.settings.keylime_tenant_service,
                        "-c",
                        "add",
                        *tenant_args,
                    ]
                )
                operation = "replace-add"
            else:
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
                        delete.get("stdout", ""),
                        applied.get("stdout", ""),
                        reactivate.get("stdout", ""),
                    ]
                    if part
                ),
                "stderr": "\n".join(
                    part
                    for part in [
                        delete.get("stderr", ""),
                        applied.get("stderr", ""),
                        reactivate.get("stderr", ""),
                    ]
                    if part
                ),
            }

    def tenant_tool_create_measured_boot_refstate(
        self,
        event_log: bytes,
        *,
        secure_boot_required: bool = True,
    ) -> dict[str, Any]:
        """Create a Keylime measured boot reference state from a TPM event log."""

        Path(self.settings.temp_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.settings.temp_dir) as tmp:
            tmp_path = Path(tmp)
            tmp_path.chmod(0o777)
            event_path = tmp_path / "binary_bios_measurements"
            output_path = tmp_path / "measured-boot-refstate.json"
            event_path.write_bytes(event_log)
            output_path.touch(mode=0o666)
            modern_command = [
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
                "measured-boot",
                "-e",
                "/keylime-openstack-tmp/binary_bios_measurements",
                "-o",
                "/keylime-openstack-tmp/measured-boot-refstate.json",
            ]
            command_attempts = [modern_command]
            if not secure_boot_required:
                command_attempts = [
                    [*modern_command, "--without-secureboot"],
                    [*modern_command, "-i"],
                    modern_command,
                ]
            result = {"rc": 1, "stdout": "", "stderr": "measured boot policy generation not run"}
            attempt_errors: list[str] = []
            for command in command_attempts:
                output_path.write_text("", encoding="utf-8")
                result = self._run_tenant_tool(command)
                if result["rc"] == 0:
                    break
                attempt_errors.append(result["stderr"] or result["stdout"])

            if result["rc"] != 0:
                legacy_command = [
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
                output_path.write_text("", encoding="utf-8")
                result = self._run_tenant_tool(legacy_command)
                if result["rc"] != 0:
                    detail = result["stderr"] or result["stdout"]
                    attempt_errors.append(detail)
                    raise RuntimeError(
                        "Keylime measured boot reference-state generation failed. "
                        "Tried keylime-policy create measured-boot and legacy "
                        f"create_mb_refstate. Details: {_join_errors(attempt_errors)}"
                    )
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

    def _agent_list_urls(self) -> list[tuple[str, str]]:
        urls: list[tuple[str, str]] = []
        for base, source, versions in (
            (self.settings.keylime_verifier_url, "verifier-api", ("v2.5",)),
            (self.settings.keylime_registrar_url, "registrar-api", ("v2.1", "v2.2", "v2.5")),
        ):
            normalized_base = base.rstrip("/")
            if not normalized_base:
                continue
            for version in versions:
                urls.append((f"{normalized_base}/{version}/agents/", source))
                urls.append((f"{normalized_base}/{version}/agents", source))
            parts = urlsplit(normalized_base)
            if parts.scheme == "http" and parts.port in {8881, 8891}:
                https_base = urlunsplit(("https", parts.netloc, parts.path.rstrip("/"), "", ""))
                for version in versions:
                    urls.append((f"{https_base}/{version}/agents/", source))
                    urls.append((f"{https_base}/{version}/agents", source))
        return list(dict.fromkeys(urls))

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
            return {
                "rc": 1,
                "stdout": "",
                "stderr": "tenant tool fallback disabled",
                "command": command,
            }
        if command and command[0] == "docker" and shutil.which("docker") is None:
            return {
                "rc": 127,
                "stdout": "",
                "stderr": (
                    "docker CLI is unavailable in the API/worker container. "
                    "Mount HOST_DOCKER_BIN, HOST_DOCKER_COMPOSE_PLUGIN, and "
                    "/var/run/docker.sock, or disable KEYLIME_TENANT_TOOL_ENABLED "
                    "when tenant-tool policy operations are not required."
                ),
                "command": command,
            }
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
            return {"rc": 127, "stdout": "", "stderr": _exception_summary(exc), "command": command}
        except subprocess.TimeoutExpired as exc:
            return {
                "rc": 124,
                "stdout": exc.stdout or "",
                "stderr": _exception_summary(exc),
                "command": command,
            }
        return {
            "rc": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "command": command,
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


def normalize_agent_inventory_payload(payload: Any, *, source: str = "") -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    _collect_agent_inventory(payload, records, inherited_uuid="", source=source)
    return _dedupe_agent_inventory(records)


def parse_tenant_reglist_stdout(stdout: str) -> list[dict[str, Any]]:
    text = str(stdout or "").strip()
    records: list[dict[str, Any]] = []
    for candidate in _structured_payload_candidates(text):
        for loader in (json.loads, literal_eval):
            try:
                parsed = loader(candidate)
            except Exception:
                continue
            records.extend(normalize_agent_inventory_payload(parsed, source="tenant-tool-reglist"))
            if records:
                return _dedupe_agent_inventory(records)
    return _dedupe_agent_inventory(_parse_tenant_reglist_text(text))


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


def _collect_agent_inventory(
    value: Any,
    records: list[dict[str, Any]],
    *,
    inherited_uuid: str,
    source: str,
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).strip()
            if UUID_RE.fullmatch(key_text):
                _collect_agent_inventory(item, records, inherited_uuid=key_text, source=source)

        normalized = {_normalize_key(key): item for key, item in value.items()}
        metadata = _metadata_dict(normalized.get("metadata") or normalized.get("meta_data"))
        agent_uuid = str(
            normalized.get("uuid")
            or normalized.get("agent_uuid")
            or normalized.get("agent_id")
            or normalized.get("agentid")
            or inherited_uuid
            or ""
        ).strip()
        ip = _first_ip(
            normalized,
            (
                "ip",
                "agent_ip",
                "contact_ip",
                "host_ip",
                "address",
                "registrar_ip",
                "verifier_ip",
            ),
        )
        port = _first_int(normalized, ("port", "agent_port", "contact_port"))
        hostname = str(
            normalized.get("hostname")
            or normalized.get("host")
            or normalized.get("node")
            or metadata.get("hostname")
            or metadata.get("host")
            or ""
        ).strip()
        if UUID_RE.fullmatch(agent_uuid):
            records.append(
                {
                    "agent_uuid": agent_uuid,
                    "ip": ip,
                    "port": port,
                    "hostname": hostname,
                    "metadata": metadata,
                    "source": source,
                }
            )
        for item in value.values():
            _collect_agent_inventory(item, records, inherited_uuid=agent_uuid, source=source)
        return

    if isinstance(value, list):
        for item in value:
            _collect_agent_inventory(item, records, inherited_uuid=inherited_uuid, source=source)


def _parse_tenant_reglist_text(stdout: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current_uuid = ""
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        uuids = UUID_RE.findall(line)
        ips = [_valid_ip(item) for item in IP_RE.findall(line)]
        ips = [item for item in ips if item]
        if uuids:
            current_uuid = uuids[-1]
        if uuids and ips:
            records.append(
                {
                    "agent_uuid": uuids[-1],
                    "ip": ips[-1],
                    "port": None,
                    "hostname": "",
                    "metadata": {},
                    "source": "tenant-tool-reglist",
                }
            )
            continue
        if current_uuid and ips and re.search(
            r"\b(contact[_ -]?ip|agent[_ -]?ip|host[_ -]?ip|ip|address)\b",
            line,
            re.IGNORECASE,
        ):
            records.append(
                {
                    "agent_uuid": current_uuid,
                    "ip": ips[-1],
                    "port": None,
                    "hostname": "",
                    "metadata": {},
                    "source": "tenant-tool-reglist",
                }
            )
    return records


def _structured_payload_candidates(text: str) -> list[str]:
    candidates = [text] if text else []
    starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
    if starts:
        candidates.append(text[min(starts) :])
    return [item for item in dict.fromkeys(candidates) if item]


def _dedupe_agent_inventory(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        agent_uuid = str(record.get("agent_uuid") or "").strip()
        if not UUID_RE.fullmatch(agent_uuid):
            continue
        current = merged.setdefault(
            agent_uuid,
            {
                "agent_uuid": agent_uuid,
                "ip": "",
                "port": None,
                "hostname": "",
                "metadata": {},
                "source": "",
            },
        )
        for key in ("ip", "hostname", "source"):
            if record.get(key) and not current.get(key):
                current[key] = record[key]
        if record.get("port") and not current.get("port"):
            current["port"] = record["port"]
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        if metadata:
            current["metadata"] = {**current.get("metadata", {}), **metadata}
    return sorted(merged.values(), key=lambda item: item["agent_uuid"])


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace(" ", "_").replace("-", "_")


def _metadata_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return {str(key): item for key, item in parsed.items()}
    return {}


def _first_ip(values: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        candidate = _valid_ip(values.get(key))
        if candidate:
            return candidate
    return ""


def _valid_ip(value: Any) -> str:
    text = str(value or "").strip()
    if not IP_RE.fullmatch(text):
        return ""
    parts = text.split(".")
    if all(0 <= int(part) <= 255 for part in parts):
        return text
    return ""


def _first_int(values: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = values.get(key)
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    return f"{exc.__class__.__module__}.{exc.__class__.__name__}"


def _join_errors(errors: list[str]) -> str:
    normalized = [item.strip() for item in errors if item and item.strip()]
    if not normalized:
        return "no error output"
    return " | ".join(dict.fromkeys(normalized))
