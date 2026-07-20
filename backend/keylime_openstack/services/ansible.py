"""Constrained Ansible adapter for compute-node policy operations."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from keylime_openstack.config import Settings
from keylime_openstack.models import ComputeNode


SAFE_HOST = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_ADDRESS = re.compile(r"^[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class AnsibleResult:
    rc: int
    stdout: str
    stderr: str


class AnsibleExecutor:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run(
        self,
        *,
        playbook: str,
        node: ComputeNode,
        workspace: Path,
        extra_vars: dict[str, Any],
    ) -> AnsibleResult:
        if not self.settings.ansible_enabled:
            raise RuntimeError("Ansible policy execution is disabled")
        if not shutil.which(self.settings.ansible_binary):
            raise RuntimeError(f"Ansible executable not found: {self.settings.ansible_binary}")
        if not shutil.which("ssh"):
            raise RuntimeError(
                "OpenSSH client is required by the Ansible SSH connection plugin. "
                "Set INSTALL_OS_TOOLS=true in /etc/keylime-openstack/keylime-openstack.env "
                "and rebuild the api/worker image."
            )
        self._validate_ssh_files()
        self._validate_node(node)
        playbook_path = (self.settings.ansible_playbook_path / playbook).resolve()
        if playbook_path.parent != self.settings.ansible_playbook_path.resolve():
            raise RuntimeError(f"invalid Ansible playbook path: {playbook}")
        if not playbook_path.is_file():
            raise RuntimeError(f"Ansible playbook not found: {playbook_path}")

        workspace.mkdir(parents=True, exist_ok=True)
        inventory_path = workspace / "inventory.ini"
        vars_path = workspace / "extra-vars.json"
        inventory_path.write_text(self._inventory(node), encoding="utf-8")
        vars_path.write_text(json.dumps(extra_vars, ensure_ascii=False), encoding="utf-8")

        command = [
            self.settings.ansible_binary,
            "-i",
            str(inventory_path),
            str(playbook_path),
            "--limit",
            node.hostname,
            "--extra-vars",
            f"@{vars_path}",
        ]
        env = {
            **os.environ,
            "ANSIBLE_HOST_KEY_CHECKING": "True",
            "ANSIBLE_RETRY_FILES_ENABLED": "False",
            "ANSIBLE_NOCOLOR": "True",
        }
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.ansible_timeout_seconds,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"Ansible executable not found: {self.settings.ansible_binary}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Ansible playbook timed out after {exc.timeout} seconds") from exc
        return AnsibleResult(completed.returncode, completed.stdout, completed.stderr)

    def _validate_ssh_files(self) -> None:
        configured_files = {
            "Ansible SSH private key": self.settings.ansible_ssh_private_key_file,
            "Ansible known_hosts": self.settings.ansible_known_hosts_file,
        }
        for label, value in configured_files.items():
            if value and not Path(value).is_file():
                raise RuntimeError(f"{label} not found: {value}")
        private_key = self.settings.ansible_ssh_private_key_file
        if private_key and Path(private_key).stat().st_mode & 0o077:
            raise RuntimeError(f"Ansible SSH private key permissions must be 0600: {private_key}")

    def _inventory(self, node: ComputeNode) -> str:
        address = node.management_ip or node.keylime_agent_ip
        fields = [
            node.hostname,
            f"ansible_host={address}",
            f"ansible_user={self.settings.ansible_remote_user}",
        ]
        if self.settings.ansible_ssh_private_key_file:
            fields.append(
                f"ansible_ssh_private_key_file={self.settings.ansible_ssh_private_key_file}"
            )
        if self.settings.ansible_known_hosts_file:
            fields.append(
                "ansible_ssh_common_args='-o StrictHostKeyChecking=yes -o "
                f"UserKnownHostsFile={self.settings.ansible_known_hosts_file}"
                + (" -o IdentitiesOnly=yes'" if self.settings.ansible_ssh_private_key_file else "'")
            )
        return "[keylime_managed]\n" + " ".join(fields) + "\n"

    @staticmethod
    def _validate_node(node: ComputeNode) -> None:
        address = node.management_ip or node.keylime_agent_ip
        if not SAFE_HOST.fullmatch(node.hostname):
            raise RuntimeError(f"unsafe Ansible hostname: {node.hostname}")
        if not address or not SAFE_ADDRESS.fullmatch(address):
            raise RuntimeError(f"unsafe management address for node {node.hostname}")
