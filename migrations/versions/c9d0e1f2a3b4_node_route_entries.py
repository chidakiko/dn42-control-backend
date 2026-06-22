"""node route entries table

把逐路由明细从 ``node_routing.routes`` 单个 JSON 列拆到独立的 ``node_route_entries``
表：前缀检索走 SQL + 索引，写入按内容哈希门控。``node_routing.routes`` 列保留（恒
写 NULL），不做破坏性删除。

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-18 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "node_route_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=64), nullable=False),
        sa.Column("is_v6", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("local", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("primary", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("origin_asn", sa.BigInteger(), nullable=True),  # DN42 ASN 超 int32
        sa.Column("protocol", sa.String(length=128), nullable=True),
        sa.Column("rpki", sa.String(length=16), nullable=True),
        sa.Column("next_hop", sa.String(length=128), nullable=True),
        sa.Column("as_path", sa.JSON(), nullable=True),
        sa.Column("communities", sa.JSON(), nullable=True),
        sa.Column("large_communities", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["nodes.node_id"],
            name=op.f("fk_node_route_entries_node_id_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_node_route_entries")),
    )
    op.create_index("ix_node_route_entries_node_v6", "node_route_entries", ["node_id", "is_v6"])
    op.create_index("ix_node_route_entries_node_local", "node_route_entries", ["node_id", "local"])
    op.create_index(
        "ix_node_route_entries_node_prefix", "node_route_entries", ["node_id", "prefix"]
    )


def downgrade() -> None:
    op.drop_index("ix_node_route_entries_node_prefix", table_name="node_route_entries")
    op.drop_index("ix_node_route_entries_node_local", table_name="node_route_entries")
    op.drop_index("ix_node_route_entries_node_v6", table_name="node_route_entries")
    op.drop_table("node_route_entries")
