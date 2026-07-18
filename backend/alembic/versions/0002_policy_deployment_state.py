"""add formal policy deployment state

Revision ID: 0002_policy_deployment_state
Revises: 0001_initial_trust_plane
Create Date: 2026-07-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_policy_deployment_state"
down_revision = "0001_initial_trust_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "policy_bindings",
        sa.Column("executor", sa.String(length=40), nullable=False, server_default="ansible"),
    )
    op.add_column(
        "policy_bindings",
        sa.Column(
            "application_status",
            sa.String(length=40),
            nullable=False,
            server_default="queued",
        ),
    )
    op.add_column(
        "policy_bindings",
        sa.Column("external_policy_name", sa.String(length=255), nullable=False, server_default=""),
    )
    op.add_column(
        "policy_bindings",
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "policy_bindings",
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "policy_bindings",
        sa.Column("rendered_policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index(
        "ix_policy_bindings_application_status",
        "policy_bindings",
        ["application_status"],
    )
    op.execute("UPDATE policy_bindings SET application_status = 'not_deployed'")
    op.execute(
        "UPDATE trust_policies SET policy_type = 'measured_boot' "
        "WHERE policy_type IN ('boot', 'tpm_pcr')"
    )
    op.execute(
        "UPDATE trust_policies SET policy_type = 'ima_runtime' "
        "WHERE policy_type = 'runtime'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE trust_policies SET policy_type = 'tpm_pcr' "
        "WHERE policy_type = 'measured_boot'"
    )
    op.drop_index("ix_policy_bindings_application_status", table_name="policy_bindings")
    op.drop_column("policy_bindings", "rendered_policy")
    op.drop_column("policy_bindings", "last_error")
    op.drop_column("policy_bindings", "applied_at")
    op.drop_column("policy_bindings", "external_policy_name")
    op.drop_column("policy_bindings", "application_status")
    op.drop_column("policy_bindings", "executor")
