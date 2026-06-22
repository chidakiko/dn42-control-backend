"""shared dns groups (records model; replace per-node dns_zones)

DNS 从节点级 ``dns_zones`` 改为记录为中心的共享组：``dns_groups``（name + bind_addresses）
+ ``dns_group_zones``（组声明的权威 zone + 可选 SOA 覆盖）+ ``dns_records``（扁平记录：
name/type/content/ttl/comment）。节点经新增 ``nodes.dns_group_id`` 订阅。多节点同组 ⇒ anycast。

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-19 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts(name: str) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("(CURRENT_TIMESTAMP)"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "dns_groups",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("bind_addresses", sa.JSON(), nullable=False),
        sa.Column("cache_ttl_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("forwards", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dns_groups")),
        sa.UniqueConstraint("name", name=op.f("uq_dns_groups_name")),
    )
    op.create_table(
        "dns_group_zones",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dns_group_id", sa.Integer(), nullable=False),
        sa.Column("zone", sa.String(length=255), nullable=False),
        sa.Column("primary_ns", sa.String(length=255), nullable=True),
        sa.Column("admin_email", sa.String(length=255), nullable=True),
        sa.Column("soa_refresh", sa.Integer(), nullable=True),
        sa.Column("soa_retry", sa.Integer(), nullable=True),
        sa.Column("soa_expire", sa.Integer(), nullable=True),
        sa.Column("soa_minimum", sa.Integer(), nullable=True),
        sa.Column("default_ttl", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["dns_group_id"],
            ["dns_groups.id"],
            name=op.f("fk_dns_group_zones_dns_group_id_dns_groups"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dns_group_zones")),
        sa.UniqueConstraint("dns_group_id", "zone", name="uq_dns_group_zones_group_id_zone"),
    )
    op.create_index("ix_dns_group_zones_dns_group_id", "dns_group_zones", ["dns_group_id"])
    op.create_table(
        "dns_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dns_group_zone_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("ttl", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.ForeignKeyConstraint(
            ["dns_group_zone_id"],
            ["dns_group_zones.id"],
            name=op.f("fk_dns_records_dns_group_zone_id_dns_group_zones"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dns_records")),
    )
    op.create_index("ix_dns_records_dns_group_zone_id", "dns_records", ["dns_group_zone_id"])

    # 仅普通整数列（SQLite 不支持 ALTER ADD COLUMN 带 FK，且默认不强制 FK）。
    op.add_column("nodes", sa.Column("dns_group_id", sa.Integer(), nullable=True))
    op.create_index("ix_nodes_dns_group_id", "nodes", ["dns_group_id"])

    op.drop_table("dns_zones")


def downgrade() -> None:
    op.create_table(
        "dns_zones",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["node_id"],
            ["nodes.node_id"],
            name=op.f("fk_dns_zones_node_id_nodes"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dns_zones")),
        sa.UniqueConstraint("node_id", "name", name="uq_dns_zones_node_id_name"),
    )
    op.drop_index("ix_nodes_dns_group_id", table_name="nodes")
    op.drop_column("nodes", "dns_group_id")
    op.drop_index("ix_dns_records_dns_group_zone_id", table_name="dns_records")
    op.drop_table("dns_records")
    op.drop_index("ix_dns_group_zones_dns_group_id", table_name="dns_group_zones")
    op.drop_table("dns_group_zones")
    op.drop_table("dns_groups")
