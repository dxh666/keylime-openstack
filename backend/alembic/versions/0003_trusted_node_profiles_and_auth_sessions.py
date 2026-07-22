"""add trusted node profiles and auth sessions

Revision ID: 0003_trusted_node_profiles_and_auth_sessions
Revises: 0002_policy_deployment_state
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_trusted_node_profiles_and_auth_sessions"
down_revision = "0002_policy_deployment_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trusted_node_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("compute_nodes.id"), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("openstack_compute_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("management_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("is_openstack_compute", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("trust_managed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("trusted_root_type", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("adapter_type", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("agent_endpoint", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("agent_identity", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("capabilities", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("registration_status", sa.String(length=40), nullable=False, server_default="unmanaged"),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evidence_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("node_id", name="uq_trusted_node_profiles_node_id"),
    )
    op.create_index("ix_trusted_node_profiles_node_id", "trusted_node_profiles", ["node_id"])
    op.create_index("ix_trusted_node_profiles_hostname", "trusted_node_profiles", ["hostname"])
    op.create_index(
        "ix_trusted_node_profiles_openstack_compute_name",
        "trusted_node_profiles",
        ["openstack_compute_name"],
    )
    op.create_index(
        "ix_trusted_node_profiles_trusted_root_type",
        "trusted_node_profiles",
        ["trusted_root_type"],
    )
    op.create_index(
        "ix_trusted_node_profiles_registration_status",
        "trusted_node_profiles",
        ["registration_status"],
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=120), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_token_hash"),
    )
    op.create_index("ix_auth_sessions_session_token_hash", "auth_sessions", ["session_token_hash"])
    op.create_index("ix_auth_sessions_username", "auth_sessions", ["username"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_table("auth_sessions")
    op.drop_table("trusted_node_profiles")
