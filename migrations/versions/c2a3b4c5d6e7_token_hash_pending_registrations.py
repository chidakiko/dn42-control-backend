"""agent token hashing + pending registrations

Revision ID: c2a3b4c5d6e7
Revises: b1f2c3d4e5a6
Create Date: 2026-06-10 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c2a3b4c5d6e7"
down_revision: str | None = "b1f2c3d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 项目尚未发布，agent_tokens 中不存在需要回填的历史行，token_hash 直接非空。
    with op.batch_alter_table("agent_tokens") as batch:
        batch.add_column(sa.Column("token_hash", sa.String(length=128), nullable=False))
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index(
            op.f("ix_agent_tokens_token_hash"), ["token_hash"], unique=True
        )

    op.create_table(
        "pending_registrations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("requested_node_id", sa.String(length=64), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=True),
        sa.Column("inventory", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("note", sa.String(length=256), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_registrations")),
    )
    op.create_index(
        op.f("ix_pending_registrations_requested_node_id"),
        "pending_registrations",
        ["requested_node_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_pending_registrations_status"),
        "pending_registrations",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_pending_registrations_status"), table_name="pending_registrations"
    )
    op.drop_index(
        op.f("ix_pending_registrations_requested_node_id"),
        table_name="pending_registrations",
    )
    op.drop_table("pending_registrations")
    with op.batch_alter_table("agent_tokens") as batch:
        batch.drop_index(op.f("ix_agent_tokens_token_hash"))
        batch.drop_column("expires_at")
        batch.drop_column("token_hash")
