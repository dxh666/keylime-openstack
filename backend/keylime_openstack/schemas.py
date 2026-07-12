"""Pydantic response schemas used by the management API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


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


class TrustPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    policy_type: str
    version: int
    status: str
    hash_alg: str
    protected_paths: list[str]
    excludes: list[str]
    source: dict[str, Any]
    description: str


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


class OverviewOut(BaseModel):
    nodes_total: int
    nodes_trusted: int
    nodes_boot_trusted: int
    nodes_runtime_trusted: int
    nodes_untrusted: int
    latest_decisions: list[TrustDecisionOut]
    worker: dict[str, Any]
    traits: list[str]
