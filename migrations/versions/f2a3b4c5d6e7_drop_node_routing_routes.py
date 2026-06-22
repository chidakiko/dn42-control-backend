"""drop dead node_routing.routes column

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-20 00:00:00.000000

路由明细早期整张存在 ``node_routing.routes`` 单 JSON 列，已迁到索引化的
``node_route_entries`` 表（见 c9d0e1f2a3b4）。此后该列恒写 NULL，是死列。
这里彻底删掉它，消除一处不再有真相源意义的存储。

downgrade 重建空列（nullable），但历史明细不还原（已在 node_route_entries 中）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch 模式兼容 SQLite（无原生 DROP COLUMN）与 Postgres。
    with op.batch_alter_table("node_routing") as batch:
        batch.drop_column("routes")


def downgrade() -> None:
    with op.batch_alter_table("node_routing") as batch:
        batch.add_column(sa.Column("routes", sa.JSON(), nullable=True))
