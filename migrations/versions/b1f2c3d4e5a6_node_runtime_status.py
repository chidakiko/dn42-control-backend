"""node runtime status tables

Revision ID: b1f2c3d4e5a6
Revises: a0043f410bda
Create Date: 2026-06-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b1f2c3d4e5a6"
down_revision: str | None = "a0043f410bda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_status",
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("desired_generation", sa.Integer(), nullable=True),
        sa.Column("observed_generation", sa.Integer(), nullable=True),
        sa.Column("last_report_status", sa.String(length=32), nullable=True),
        sa.Column("last_apply_status", sa.String(length=32), nullable=True),
        sa.Column("drift_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("health", sa.String(length=16), nullable=False, server_default="unknown"),
        sa.Column("last_snapshot", sa.JSON(), nullable=True),
        sa.Column("last_report", sa.JSON(), nullable=True),
        sa.Column("last_apply", sa.JSON(), nullable=True),
        sa.Column("last_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_report_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_apply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["nodes.node_id"],
            name=op.f("fk_node_status_node_id_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("node_id", name=op.f("pk_node_status")),
    )
    op.create_table(
        "node_status_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["nodes.node_id"],
            name=op.f("fk_node_status_events_node_id_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_node_status_events")),
    )
    op.create_index(
        op.f("ix_node_status_events_node_id"),
        "node_status_events",
        ["node_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_node_status_events_kind"),
        "node_status_events",
        ["kind"],
        unique=False,
    )
    op.create_index(
        op.f("ix_node_status_events_created_at"),
        "node_status_events",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_node_status_events_created_at"), table_name="node_status_events")
    op.drop_index(op.f("ix_node_status_events_kind"), table_name="node_status_events")
    op.drop_index(op.f("ix_node_status_events_node_id"), table_name="node_status_events")
    op.drop_table("node_status_events")
    op.drop_table("node_status")
