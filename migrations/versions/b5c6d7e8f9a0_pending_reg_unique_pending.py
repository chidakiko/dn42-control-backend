"""pending_registrations: 一节点最多一条 pending 行（防并发注册竞态）

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-06-23 08:00:00.000000

并发的两个 /agent/register（同一未知节点）在 PostgreSQL MVCC 下会都 SELECT-miss 再各插
一条 pending 行，污染审批门（status_for 取最新行，审批状态变不确定）；SQLite 单写者把这窗口
藏住了。加 partial unique index「(requested_node_id) WHERE status='pending'」从 DB 层兜住——
败者撞 IntegrityError，record() 重试走 update。不约束非 pending 行，故 reject 后可重新注册。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_pending_registrations_node_pending",
        "pending_registrations",
        ["requested_node_id"],
        unique=True,
        sqlite_where=sa.text("status = 'pending'"),
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_pending_registrations_node_pending", table_name="pending_registrations"
    )
