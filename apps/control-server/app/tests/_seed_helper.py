from __future__ import annotations

"""测试用：把 bootstrap demo 节点 seed 进指定的 SQLite 文件库。

生产 lifespan **不再** seed（空库）；需要预置节点的测试显式调用本 helper（用独立
engine / event loop 写文件库，之后 app 起 lifespan 连同一文件即可读到）。
"""

import asyncio

from app.core.config import ControlServerConfig
from app.db import Base, Database, seed_initial_data


async def _seed_db_async(config: ControlServerConfig) -> None:
    database = Database(config.database_url)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            await seed_initial_data(session, config)
    finally:
        await database.engine.dispose()


def seed_test_db(config: ControlServerConfig) -> None:
    """同步入口（独立 event loop 跑一次 seed）。"""

    asyncio.run(_seed_db_async(config))
