"""Alembic 迁移环境。

- DSN 优先从 ``DN42_CONTROL_DATABASE_URL`` 取，与运行时配置共享同一变量；
- 同时支持 sync 与 async DSN：把 ``+aiosqlite`` / ``+asyncpg`` / ``+asyncmy``
  剥掉，落到对应的同步驱动上跑迁移（migration 期间不需要异步）。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (
    REPO_ROOT / "apps" / "control-server",
    REPO_ROOT / "packages" / "dn42_schemas",
    REPO_ROOT / "packages" / "dn42_common",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.db.models import Base  # noqa: E402  (sys.path 注入后才能导入)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    """优先读环境变量；统一退回到同步驱动。"""

    url = os.environ.get("DN42_CONTROL_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if url is None:
        raise RuntimeError("database url not configured")
    # 把 async driver 替换为对应 sync driver。
    replacements = {
        "+aiosqlite": "",
        "+asyncpg": "+psycopg2",
        "+asyncmy": "+pymysql",
    }
    for needle, replacement in replacements.items():
        if needle in url:
            url = url.replace(needle, replacement)
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _resolve_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
