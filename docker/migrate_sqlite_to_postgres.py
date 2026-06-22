#!/usr/bin/env python3
"""一次性把 control-server 的 SQLite 库整库迁移到 PostgreSQL。

按 SQLAlchemy 模型的 **FK 拓扑序**逐表拷贝行（复用同一份 schema，类型安全：JSON /
Boolean / DateTime 经模型列类型正确往返），拷完**重置 Postgres 自增序列**（否则后续
INSERT 会主键冲突）。**源库全程只读**。

在 control-server 镜像内运行（已含 asyncpg + app.db.models）：
  python deploy/migrate_sqlite_to_postgres.py \
      --src sqlite+aiosqlite:////data/control.db \
      --dst postgresql+asyncpg://dn42:PASS@postgres:5432/dn42_control

默认拒绝向非空目标迁移（防重复导入）；确需重灌请先清库再加 --force。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
import app.db.models  # noqa: F401 - 导入即把所有模型注册到 Base.metadata

_INT_PK_PREFIXES = ("INT", "BIGINT", "SMALLINT")


async def _count(conn, table) -> int:
    return (await conn.execute(select(func.count()).select_from(table))).scalar_one()


def _autoinc_pk(table) -> str | None:
    """单列整数主键的列名（需重置 Postgres 序列）；否则 None。"""

    pk = list(table.primary_key.columns)
    if len(pk) == 1 and str(pk[0].type).upper().startswith(_INT_PK_PREFIXES):
        return pk[0].name
    return None


async def migrate(src_url: str, dst_url: str, *, force: bool) -> int:
    src = create_async_engine(src_url)
    dst = create_async_engine(dst_url)
    try:
        # 1) 目标建 schema（幂等；与 create_all / alembic 等价）。
        async with dst.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # 2) 非空目标保护。
        if not force:
            async with dst.connect() as conn:
                for table in Base.metadata.sorted_tables:
                    if await _count(conn, table) > 0:
                        print(
                            f"!! 目标表 {table.name} 已有数据，拒绝迁移"
                            f"（重灌请先清库后加 --force）"
                        )
                        return 2

        # 3) 逐表拷贝（FK 拓扑序，保证父表先于子表）。
        total = 0
        for table in Base.metadata.sorted_tables:
            async with src.connect() as sconn:
                rows = [dict(r) for r in (await sconn.execute(table.select())).mappings()]
            if rows:
                async with dst.begin() as dconn:
                    await dconn.execute(table.insert(), rows)
                total += len(rows)
            print(f"  {table.name}: {len(rows)}")

        # 4) 重置自增序列：setval(seq, max(id), is_called=有行)。空表则归位到 1/未调用。
        async with dst.begin() as conn:
            for table in Base.metadata.sorted_tables:
                col = _autoinc_pk(table)
                if col is None:
                    continue
                await conn.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col}'), "
                        f"COALESCE((SELECT MAX({col}) FROM {table.name}), 1), "
                        f"(SELECT COUNT(*) FROM {table.name}) > 0)"
                    )
                )

        print(f">> 迁移完成：{total} 行，自增序列已重置")
        return 0
    finally:
        await src.dispose()
        await dst.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → PostgreSQL 整库迁移（control-server）")
    parser.add_argument("--src", required=True, help="源 SQLite 异步 DSN")
    parser.add_argument("--dst", required=True, help="目标 PostgreSQL 异步 DSN")
    parser.add_argument("--force", action="store_true", help="目标已有数据时仍继续（危险）")
    args = parser.parse_args()
    return asyncio.run(migrate(args.src, args.dst, force=args.force))


if __name__ == "__main__":
    sys.exit(main())
