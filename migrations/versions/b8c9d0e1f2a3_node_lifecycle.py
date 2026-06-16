"""add nodes.lifecycle for decommission flow

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-13 02:00:00.000000

节点退役收敛:新增 ``nodes.lifecycle`` 列(active / decommissioned)。退役态下
materialize 产出空 interfaces/bgp/dns,节点停止宣告路由;删节点前必须先退役,
避免直接删库留下仍在宣告路由的孤儿。存量行默认 ``active``。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nodes",
        sa.Column(
            "lifecycle",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
    )


def downgrade() -> None:
    op.drop_column("nodes", "lifecycle")
