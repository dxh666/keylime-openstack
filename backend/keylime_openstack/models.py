"""SQLAlchemy data model for trusted compute decisions and policy state."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from keylime_openstack.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class HardwareProfile(TimestampMixin, Base):
    __tablename__ = "hardware_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    vendor: Mapped[str] = mapped_column(String(120), default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    kernel_family: Mapped[str] = mapped_column(String(120), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    nodes: Mapped[list["ComputeNode"]] = relationship(back_populates="hardware_profile")


class ComputeNode(TimestampMixin, Base):
    __tablename__ = "compute_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hypervisor_name: Mapped[str] = mapped_column(String(255), default="", index=True)
    management_ip: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(64), default="compute")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    keylime_agent_uuid: Mapped[str] = mapped_column(String(64), default="")
    keylime_agent_ip: Mapped[str] = mapped_column(String(64), default="")
    keylime_agent_port: Mapped[int] = mapped_column(Integer, default=9002)
    hardware_profile_id: Mapped[int | None] = mapped_column(ForeignKey("hardware_profiles.id"))
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    hardware_profile: Mapped[HardwareProfile | None] = relationship(back_populates="nodes")
    decisions: Mapped[list["TrustDecision"]] = relationship(back_populates="node")


class TrustPolicy(TimestampMixin, Base):
    __tablename__ = "trust_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    policy_type: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="draft")
    hash_alg: Mapped[str] = mapped_column(String(40), default="sha256")
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    protected_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    excludes: Mapped[list[str]] = mapped_column(JSON, default=list)
    source: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")

    bindings: Mapped[list["PolicyBinding"]] = relationship(back_populates="policy")


class PolicyBinding(TimestampMixin, Base):
    __tablename__ = "policy_bindings"
    __table_args__ = (
        UniqueConstraint("policy_id", "target_type", "target_id", name="uq_policy_binding_target"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    policy_id: Mapped[int] = mapped_column(ForeignKey("trust_policies.id"), index=True)
    target_type: Mapped[str] = mapped_column(String(40), index=True)
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    binding_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    policy: Mapped[TrustPolicy] = relationship(back_populates="bindings")


class EvidenceRecord(TimestampMixin, Base):
    __tablename__ = "evidence_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("compute_nodes.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    evidence_type: Mapped[str] = mapped_column(String(80), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(40), default="unknown")
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OpenStackState(TimestampMixin, Base):
    __tablename__ = "openstack_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("compute_nodes.id"), index=True)
    resource_provider_uuid: Mapped[str] = mapped_column(String(64), default="")
    service_binary: Mapped[str] = mapped_column(String(80), default="nova-compute")
    service_status: Mapped[str] = mapped_column(String(40), default="unknown")
    service_state: Mapped[str] = mapped_column(String(40), default="unknown")
    vm_count: Mapped[int] = mapped_column(Integer, default=0)
    traits: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TrustDecision(TimestampMixin, Base):
    __tablename__ = "trust_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("compute_nodes.id"), index=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    boot_trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    runtime_trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(String(255), default="")
    desired_traits: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    decision_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    node: Mapped[ComputeNode] = relationship(back_populates="decisions")


class TaskRun(TimestampMixin, Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    target: Mapped[str] = mapped_column(String(255), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[str] = mapped_column(String(120), default="system")
    task_args: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class AuditEvent(TimestampMixin, Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    actor: Mapped[str] = mapped_column(String(120), default="system")
    target: Mapped[str] = mapped_column(String(255), default="")
    severity: Mapped[str] = mapped_column(String(40), default="info")
    message: Mapped[str] = mapped_column(Text, default="")
    event_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class VmRiskMarker(TimestampMixin, Base):
    __tablename__ = "vm_risk_markers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    server_id: Mapped[str] = mapped_column(String(64), index=True)
    server_name: Mapped[str] = mapped_column(String(255), default="")
    host: Mapped[str] = mapped_column(String(255), index=True)
    risk_state: Mapped[str] = mapped_column(String(80), default="unknown")
    reason: Mapped[str] = mapped_column(Text, default="")
    last_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marker_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
