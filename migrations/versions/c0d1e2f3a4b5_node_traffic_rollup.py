"""node traffic rollup table

WG 流量 5min 降采样存档表 ``node_traffic_rollup``：agent 30s 轻量采样主要进 Redis 热
窗口，这张小表是其持久化存档（Redis 失效后仍能画 5min 粒度历史）。每节点每 5min 桶
一行，累加瞬时速率之和 + 次数，读时取均值。

Revision ID: c0d1e2f3a4b5
Revises: b5c6d7e8f9a0
Create Date: 2026-06-27 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_traffic_rollup",
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("bucket_start", sa.BigInteger(), nullable=False),
        sa.Column("rx_rate_sum", sa.Float(), server_default="0", nullable=False),
        sa.Column("tx_rate_sum", sa.Float(), server_default="0", nullable=False),
        sa.Column("sample_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["nodes.node_id"],
            name=op.f("fk_node_traffic_rollup_node_id_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("node_id", "bucket_start", name=op.f("pk_node_traffic_rollup")),
    )


def downgrade() -> None:
    op.drop_table("node_traffic_rollup")
