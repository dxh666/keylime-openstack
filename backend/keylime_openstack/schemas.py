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


class ComputeNodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hostname: str
    hypervisor_name: str
    management_ip: str
    role: str
    enabled: bool
    keylime_agent_uuid: str
    keylime_agent_ip: str
    keylime_agent_port: int
    facts: dict[str, Any]
    hardware_profile: HardwareProfileOut | None = None


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
