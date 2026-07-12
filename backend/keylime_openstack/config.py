"""Runtime configuration for API and worker processes."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_ENV_FILE = "/etc/keylime-openstack/keylime-openstack.env"


class Settings(BaseSettings):
    """Settings loaded from environment or /etc/keylime-openstack."""

    model_config = SettingsConfigDict(
        env_file=os.environ.get("KEYLIME_OPENSTACK_ENV_FILE", DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    service_name: str = "keylime-openstack"
    environment: str = "production"
    database_url: str = (
        "postgresql+psycopg://keylime_openstack:keylime_openstack@postgres:5432/"
        "keylime_openstack"
    )
    admin_token: str = ""
    frontend_dir: str = "/app/deploy/frontend"

    openstack_auth_mode: str = "clouds_yaml"
    openstack_cloud: str = "admin"
    openstack_clouds_yaml: str = "/etc/kolla/clouds.yaml"
    openstack_openrc: str = "/etc/kolla/admin-openrc.sh"
    openstack_cli_fallback: bool = True
    openstack_enforcement_enabled: bool = False

    keylime_verifier_url: str = "https://172.31.100.10:8881"
    keylime_registrar_url: str = "https://172.31.100.10:8891"
    keylime_api_timeout_seconds: int = 15
    keylime_tls_verify: bool = True
    keylime_tls_ca_cert: str = "/opt/keylime-docker/varlib/cv_ca/cacert.crt"
    keylime_tls_client_cert: str = "/opt/keylime-docker/varlib/cv_ca/client-cert.crt"
    keylime_tls_client_key: str = "/opt/keylime-docker/varlib/cv_ca/client-private.pem"
    keylime_tenant_tool_enabled: bool = True
    keylime_docker_dir: str = "/opt/keylime-docker"
    keylime_tenant_service: str = "keylime-tenant"

    worker_interval_seconds: int = 30
    attestation_fresh_seconds: int = 120
    legacy_trait_enabled: bool = True

    temp_dir: str = "/tmp/keylime-openstack"

    default_controller_host: str = "csri10"
    default_compute_hosts: str = "csri8,csri9,hygon22"

    dim_provider_enabled: bool = False

    @property
    def frontend_path(self) -> Path:
        return Path(self.frontend_dir)

    @property
    def temp_path(self) -> Path:
        return Path(self.temp_dir)

    @property
    def compute_host_list(self) -> list[str]:
        return [item.strip() for item in self.default_compute_hosts.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()
