"""admin audit log + enrollment token hashing

Revision ID: d4e5f6a7b8c9
Revises: c2a3b4c5d6e7
Create Date: 2026-06-11 01:00:00.000000
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # enrollment_tokens 改为哈希存储：先以可空列落地，把存量明文行
    # （仅开发库可能存在）回填为 哈希 + 派生 id，再收紧为非空唯一。
    with op.batch_alter_table("enrollment_tokens") as batch:
        batch.add_column(sa.Column("token_hash", sa.String(length=128), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT token FROM enrollment_tokens")).fetchall()
    for (plaintext,) in rows:
        digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
        bind.execute(
            sa.text(
                "UPDATE enrollment_tokens SET token_hash = :h, token = :i WHERE token = :t"
            ),
            {"h": digest, "i": f"ent_{digest[:12]}", "t": plaintext},
        )

    with op.batch_alter_table("enrollment_tokens") as batch:
        batch.alter_column("token_hash", existing_type=sa.String(length=128), nullable=False)
        batch.create_index(
            op.f("ix_enrollment_tokens_token_hash"), ["token_hash"], unique=True
        )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_log")),
    )
    op.create_index(op.f("ix_admin_audit_log_actor"), "admin_audit_log", ["actor"])
    op.create_index(op.f("ix_admin_audit_log_path"), "admin_audit_log", ["path"])
    op.create_index(
        op.f("ix_admin_audit_log_created_at"), "admin_audit_log", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_admin_audit_log_created_at"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_path"), table_name="admin_audit_log")
    op.drop_index(op.f("ix_admin_audit_log_actor"), table_name="admin_audit_log")
    op.drop_table("admin_audit_log")
    # 哈希不可逆，降级只能丢弃 token_hash；存量行保持派生 id 形态。
    with op.batch_alter_table("enrollment_tokens") as batch:
        batch.drop_index(op.f("ix_enrollment_tokens_token_hash"))
        batch.drop_column("token_hash")
