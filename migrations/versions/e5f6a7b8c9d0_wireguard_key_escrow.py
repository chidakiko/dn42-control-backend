"""node-level wireguard public key + private key escrow

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-11 02:00:00.000000

一节点一把 WireGuard 私钥（所有 peer 共用），故公钥/托管密文是节点级事实，
落在 ``nodes`` 表而非 ``wg_interfaces``。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 两列均可空、纯增量：存量节点在 agent 下次上报密钥前保持 NULL，不影响现有行为。
    with op.batch_alter_table("nodes") as batch:
        batch.add_column(sa.Column("wireguard_public_key", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("wireguard_private_key_escrow", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("nodes") as batch:
        batch.drop_column("wireguard_private_key_escrow")
        batch.drop_column("wireguard_public_key")
