"""drop dead rpki_unknown columns

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-21 00:00:00.000000

RPKI「未知」三态早已从协议移除（迁移 e1f2a3b4c5d6 剥掉了 prefilter 的 ``unknown`` 键）。
此后 ``node_routing.rpki_unknown`` 与 ``node_routing_events.rpki_unknown`` 恒写 0、无任何读取点，
是只写死列。这里彻底删掉，与 f2a3b4c5d6e7 删 ``routes`` 列同套路。

⚠️ 部署注意：这两列是 NOT NULL 无 server-default。新代码（模型已删该字段）INSERT 时不再写它，
因此**列必须先于新代码删除**，否则旧表上的 NOT NULL 约束会让 INSERT 失败。alembic 管理的库由本
迁移处理；create_all 的库（pvg2）须手动 ``ALTER TABLE ... DROP COLUMN rpki_unknown`` 后再上新代码。

downgrade 重建空列（nullable），历史「未知」计数不还原（语义已不存在）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("node_routing", "node_routing_events")


def upgrade() -> None:
    # batch 模式兼容 SQLite（无原生 DROP COLUMN）与 Postgres。
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("rpki_unknown")


def downgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("rpki_unknown", sa.Integer(), nullable=True))
