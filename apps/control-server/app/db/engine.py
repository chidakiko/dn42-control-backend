from __future__ import annotations

"""异步数据库引擎装配。

控制服务器对 DB 引擎的所有访问都通过 `Database` 实例。FastAPI 在 lifespan
中创建一次实例并附在 ``app.state``；测试可以构造独立实例并替换以隔离。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """封装 ``AsyncEngine`` + ``async_sessionmaker``，统一管理生命周期。"""

    def __init__(self, url: str, *, echo: bool = False) -> None:
        connect_args: dict[str, object] = {}
        if url.startswith("sqlite"):
            # SQLite 默认 same-thread 限制对异步连接没意义；async + 多任务下必须放开。
            connect_args["check_same_thread"] = False
        self._engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            future=True,
            connect_args=connect_args,
            pool_pre_ping=True,
        )
        if url.startswith("sqlite"):
            # SQLite 默认 foreign_keys=OFF：不开就让所有 ``ondelete`` 子句变成死字，
            # 删节点后 Peering.remote_node_id / EnrollmentToken.node_id 等外键悬挂。
            # 每个新连接都要重新开启（PRAGMA 是连接级的）。
            @event.listens_for(self._engine.sync_engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_conn, _record):  # noqa: ANN001
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        self._sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """单事务作用域：进入即开 session，退出时按异常情况 commit 或 rollback。"""

        session = self._sessionmaker()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def dispose(self) -> None:
        await self._engine.dispose()


__all__ = ["Database"]
