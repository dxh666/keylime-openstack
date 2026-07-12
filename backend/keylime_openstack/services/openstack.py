"""OpenStack SDK and Placement API adapter."""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import Any

from keylime_openstack.config import Settings
from keylime_openstack.constants import DEFAULT_TRUST_TRAITS


class OpenStackClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self):
        import openstack

        old_clouds = os.environ.get("OS_CLIENT_CONFIG_FILE")
        if self.settings.openstack_clouds_yaml:
            os.environ["OS_CLIENT_CONFIG_FILE"] = self.settings.openstack_clouds_yaml
        try:
            return openstack.connect(cloud=self.settings.openstack_cloud)
        finally:
            if old_clouds is None:
                os.environ.pop("OS_CLIENT_CONFIG_FILE", None)
            else:
                os.environ["OS_CLIENT_CONFIG_FILE"] = old_clouds

    def list_compute_services(self) -> list[dict[str, Any]]:
        conn = self.connect()
        services = conn.compute.services(binary="nova-compute")
        return [service.to_dict() for service in services]

    def set_compute_service_enabled(self, host: str, enabled: bool, reason: str = "") -> dict[str, Any]:
        conn = self.connect()
        status = "enabled" if enabled else "disabled"
        service = next(conn.compute.services(binary="nova-compute", host=host), None)
        if not service:
            return {"ok": False, "error": f"nova-compute service not found for {host}"}
        payload: dict[str, Any] = {"status": status}
        if not enabled and reason:
            payload["disabled_reason"] = reason
        updated = conn.compute.update_service(service, **payload)
        return {"ok": True, "service": updated.to_dict()}

    def set_provider_traits(self, provider_name: str, traits: list[str]) -> dict[str, Any]:
        """Set desired traits for a resource provider.

        openstacksdk support for Placement trait replacement varies by version.
        For now this method uses SDK discovery and keeps CLI fallback available
        until the target Kolla deployment is verified.
        """

        if not self.settings.openstack_enforcement_enabled:
            return {"ok": True, "skipped": True, "reason": "openstack enforcement disabled"}
        if self.settings.openstack_cli_fallback:
            return self._cli_set_provider_traits(provider_name, traits)
        return {"ok": False, "error": "Placement trait update adapter not configured"}

    def _cli_set_provider_traits(self, provider_name: str, traits: list[str]) -> dict[str, Any]:
        keylime_trait_case = "|".join(DEFAULT_TRUST_TRAITS)
        script = [
            "set -euo pipefail",
            f"source {shlex.quote(self.settings.openstack_openrc)}",
            'export OS_PLACEMENT_API_VERSION="${OS_PLACEMENT_API_VERSION:-1.17}"',
            "rp_name=$1",
            'rp_uuid=$(openstack resource provider list --name "$rp_name" -f value -c uuid | awk \'NF {print; exit}\')',
            'test -n "$rp_uuid"',
            'existing="$(openstack resource provider trait list "$rp_uuid" -f value -c name || true)"',
            "cmd=(openstack resource provider trait set)",
            'while IFS= read -r trait; do',
            '  [ -z "$trait" ] && continue',
            f'  case "$trait" in {keylime_trait_case}) continue ;; esac',
            '  cmd+=(--trait "$trait")',
            'done <<< "$existing"',
        ]
        for trait in traits:
            script.append(f"cmd+=(--trait {trait!r})")
        script.append('cmd+=("$rp_uuid")')
        script.append('"${cmd[@]}"')
        completed = subprocess.run(
            ["/usr/bin/env", "bash", "-lc", "\n".join(script), "bash", provider_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        return {
            "ok": completed.returncode == 0,
            "rc": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
