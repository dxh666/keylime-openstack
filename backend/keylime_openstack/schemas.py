"""Pydantic response schemas used by the management API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HardwareProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    vendor: str
    model: str
    kernel_family: str
    notes: str = ""


class OpenStackStateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    service_binary: str
    service_status: str
    service_state: str
    updated_at: datetime


class TrustedNodeProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    node_id: int | None = None
    hostname: str
    openstack_compute_name: str = ""
    management_ip: str = ""
    is_openstack_compute: bool = False
    trust_managed: bool = False
    trusted_root_type: str = "unknown"
    adapter_type: str = ""
    agent_endpoint: dict[str, Any] = Field(default_factory=dict)
    agent_identity: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    registration_status: str = "unmanaged"
    last_verified_at: datetime | None = None
    last_evidence_summary: dict[str, Any] = Field(default_factory=dict)


class TrustAgentRegistrationIn(BaseModel):
    hostname: str = Field(default="", max_length=255)
    openstack_compute_name: str = Field(default="", max_length=255)
    management_ip: str = Field(default="", max_length=64)
    is_openstack_compute: bool | None = None
    trust_managed: bool = False
    trusted_root_type: str = "unknown"
    adapter_type: str = ""
    agent_endpoint: dict[str, Any] = Field(default_factory=dict)
    agent_identity: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    registration_status: str = ""


class ComputeNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hostname: str
    hypervisor_name: str
    openstack_compute_name: str = ""
    management_ip: str
    role: str
    enabled: bool
    keylime_agent_uuid: str
    keylime_agent_ip: str
    keylime_agent_port: int
    trust_agent_type: str = "unmanaged"
    trust_agent_name: str = "未纳管"
    trust_managed: bool = False
    trusted_root_type: str = "unknown"
    trusted_root: str = "unknown"
    adapter_type: str = ""
    agent_endpoint: dict[str, Any] = Field(default_factory=dict)
    agent_identity: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    registration_status: str = "unmanaged"
    last_verified_at: datetime | None = None
    last_evidence_summary: dict[str, Any] = Field(default_factory=dict)
    trusted_node_profile: TrustedNodeProfileOut | None = None
    facts: dict[str, Any]
    hardware_profile: HardwareProfileOut | None = None
    openstack_state: OpenStackStateOut | None = None


class PolicyBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_type: str
    target_id: int
    target_name: str = ""
    active: bool
    executor: str
    application_status: str
    external_policy_name: str
    applied_at: datetime | None
    last_error: str
    binding_details: dict[str, Any] = Field(default_factory=dict)
    keylime_policy: dict[str, Any] = Field(default_factory=dict)


class TrustPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    policy_type: str
    version: int
    status: str
    hash_alg: str
    content: dict[str, Any]
    protected_paths: list[str]
    excludes: list[str]
    source: dict[str, Any]
    description: str
    bindings: list[PolicyBindingOut] = Field(default_factory=list)


class TrustPolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    policy_type: str = Field(default="ima_runtime", max_length=40)
    version: int = Field(default=1, ge=1)
    status: str = Field(default="draft", max_length=40)
    hash_alg: str = Field(default="sha256", max_length=40)
    content: dict[str, Any] = Field(default_factory=dict)
    protected_paths: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    target_node_ids: list[int] = Field(default_factory=list)
    deploy_now: bool = True


class TpcmDynamicGlobalSwitchIn(BaseModel):
    enabled: bool


class TpcmGlobalPolicyApplyIn(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=255)


class UserOut(BaseModel):
    username: str
    display_name: str = ""
    role: str = "admin"


class AuthStatusOut(BaseModel):
    authenticated: bool
    user: UserOut | None = None


class TrustDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    node_id: int
    decided_at: datetime
    boot_trusted: bool
    runtime_trusted: bool
    trusted: bool
    reason: str
    desired_traits: list[str]
    decision_details: dict[str, Any]


class TaskRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_type: str
    status: str
    target: str
    started_at: datetime | None
    finished_at: datetime | None
    requested_by: str
    result: dict[str, Any]
    error: str


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    event_type: str
    actor: str
    target: str
    severity: str
    message: str
    event_details: dict[str, Any]


class DashboardControlNodeOut(BaseModel):
    hostname: str
    management_ip: str
    operating_system: str
    kernel: str
    cpu_model: str
    cpu_count: int | str
    deployment_mode: str


class DashboardComponentOut(BaseModel):
    name: str
    status: str
    state: str


class DashboardOnlineUserOut(BaseModel):
    type: str
    username: str
    user_group: str
    ip_address: str


class DashboardOut(BaseModel):
    control_node: DashboardControlNodeOut
    control_plane_status: list[DashboardComponentOut]
    online_users: list[DashboardOnlineUserOut]


class HostIntegrityReportIn(BaseModel):
    hostname: str = ""
    collected_at: datetime | None = None
    cmdline: str = ""
    ima_policy: list[str] = Field(default_factory=list)
    ima_policy_error: str = ""
    ima_keyring: dict[str, Any] = Field(default_factory=dict)
    evm_keyring: dict[str, Any] = Field(default_factory=dict)
    dmesg_integrity_tail: list[str] = Field(default_factory=list)
    securityfs_mounted: bool = False
    ima_measurement_templates: dict[str, int] = Field(default_factory=dict)
    xattrs: dict[str, dict[str, str]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    facts: dict[str, Any] = Field(default_factory=dict)


class OpenTcsmEvidenceReportIn(BaseModel):
    hostname: str = ""
    collected_at: datetime | None = None
    trust_root: str = "Hygon TPCM"
    agent_name: str = "OpenTCSM"
    agent_version: str = ""
    report_type: str = "tpcm"
    boot_status: str | bool | None = None
    dynamic_measurement_status: str | bool | None = None
    runtime_status: str | bool | None = None
    ima_status: str | bool | None = None
    evm_status: str | bool | None = None
    trusted: bool | None = None
    summary: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)


class OverviewOut(BaseModel):
    nodes_total: int
    nodes_trusted: int
    nodes_boot_trusted: int
    nodes_runtime_trusted: int
    nodes_untrusted: int
    latest_decisions: list[TrustDecisionOut]
    worker: dict[str, Any]
    traits: list[str]
    trust_policy_mode: str
    trust_capabilities: dict[str, bool]
