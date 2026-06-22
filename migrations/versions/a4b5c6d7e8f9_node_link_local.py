"""add nodes.link_local for external eBGP LLA single source

Revision ID: a4b5c6d7e8f9
Revises: a3b4c5d6e7f8
Create Date: 2026-06-22 22:30:00.000000

节点级 IPv6 link-local 列(nodes.link_local)。外部 eBGP WG 接口的单一真相源：
materializer 据此派生 <link_local>/64 到这些接口 addresses（NodeSpec.link_local）。
此前 link_local 只存在于 NodeSpec / base_template，无 DB 列也无 API 入口、无法配置，
派生恒为 no-op；本列让它可经 admin API 逐节点配置。存量行默认 NULL（不派生）。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nodes", sa.Column("link_local", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("nodes", "link_local")
