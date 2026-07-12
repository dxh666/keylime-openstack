"""initial trust plane schema

Revision ID: 0001_initial_trust_plane
Revises:
Create Date: 2026-07-11
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_trust_plane"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hardware_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("vendor", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("kernel_family", sa.String(length=120), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_hardware_profiles_name", "hardware_profiles", ["name"])

    op.create_table(
        "compute_nodes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("hypervisor_name", sa.String(length=255), nullable=False),
        sa.Column("management_ip", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("keylime_agent_uuid", sa.String(length=64), nullable=False),
        sa.Column("keylime_agent_ip", sa.String(length=64), nullable=False),
        sa.Column("keylime_agent_port", sa.Integer(), nullable=False),
        sa.Column("hardware_profile_id", sa.Integer(), sa.ForeignKey("hardware_profiles.id")),
        sa.Column("facts", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("hostname"),
    )
    op.create_index("ix_compute_nodes_hostname", "compute_nodes", ["hostname"])
    op.create_index("ix_compute_nodes_hypervisor_name", "compute_nodes", ["hypervisor_name"])

    op.create_table(
        "trust_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("policy_type", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("hash_alg", sa.String(length=40), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("protected_paths", sa.JSON(), nullable=False),
        sa.Column("excludes", sa.JSON(), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_trust_policies_name", "trust_policies", ["name"])
    op.create_index("ix_trust_policies_policy_type", "trust_policies", ["policy_type"])

    op.create_table(
        "policy_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("policy_id", sa.Integer(), sa.ForeignKey("trust_policies.id"), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("binding_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_id", "target_type", "target_id", name="uq_policy_binding_target"),
    )
    op.create_index("ix_policy_bindings_policy_id", "policy_bindings", ["policy_id"])
    op.create_index("ix_policy_bindings_target_id", "policy_bindings", ["target_id"])
    op.create_index("ix_policy_bindings_target_type", "policy_bindings", ["target_type"])

    op.create_table(
        "evidence_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("compute_nodes.id"), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("evidence_type", sa.String(length=80), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_records_node_id", "evidence_records", ["node_id"])
    op.create_index("ix_evidence_records_provider", "evidence_records", ["provider"])
    op.create_index("ix_evidence_records_evidence_type", "evidence_records", ["evidence_type"])

    op.create_table(
        "openstack_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("compute_nodes.id"), nullable=False),
        sa.Column("resource_provider_uuid", sa.String(length=64), nullable=False),
        sa.Column("service_binary", sa.String(length=80), nullable=False),
        sa.Column("service_status", sa.String(length=40), nullable=False),
        sa.Column("service_state", sa.String(length=40), nullable=False),
        sa.Column("vm_count", sa.Integer(), nullable=False),
        sa.Column("traits", sa.JSON(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_openstack_states_node_id", "openstack_states", ["node_id"])

    op.create_table(
        "trust_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("compute_nodes.id"), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("boot_trusted", sa.Boolean(), nullable=False),
        sa.Column("runtime_trusted", sa.Boolean(), nullable=False),
        sa.Column("trusted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("desired_traits", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("decision_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trust_decisions_node_id", "trust_decisions", ["node_id"])

    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("requested_by", sa.String(length=120), nullable=False),
        sa.Column("task_args", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_runs_task_type", "task_runs", ["task_type"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("actor", sa.String(length=120), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("event_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])

    op.create_table(
        "vm_risk_markers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.String(length=64), nullable=False),
        sa.Column("server_name", sa.String(length=255), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("risk_state", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("last_marked_at", sa.DateTime(timezone=True)),
        sa.Column("last_cleared_at", sa.DateTime(timezone=True)),
        sa.Column("marker_details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_vm_risk_markers_server_id", "vm_risk_markers", ["server_id"])
    op.create_index("ix_vm_risk_markers_host", "vm_risk_markers", ["host"])


def downgrade() -> None:
    op.drop_table("vm_risk_markers")
    op.drop_table("audit_events")
    op.drop_table("task_runs")
    op.drop_table("trust_decisions")
    op.drop_table("openstack_states")
    op.drop_table("evidence_records")
    op.drop_table("policy_bindings")
    op.drop_table("trust_policies")
    op.drop_table("compute_nodes")
    op.drop_table("hardware_profiles")
