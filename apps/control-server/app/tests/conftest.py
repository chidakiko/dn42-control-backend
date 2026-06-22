from __future__ import annotations

"""控制服务器测试公共 fixture。

默认每条用例独占一份临时 SQLite 文件，避免互相污染。**也可整套跑在 PostgreSQL 上**：
设 ``DN42_CONTROL_TEST_DATABASE_URL=postgresql+asyncpg://…`` 即所有用例改用该 PG 库，
每用例前 drop+create 全表清场（用例串行，互不污染）。这样 SQLite 宽松藏住、PG 严格暴露的
那类 bug（int32 溢出、``FOR UPDATE`` 锁外连接可空侧等）能被现有用例直接抓到——CI 据此加了
一个 PostgreSQL job 跑同一套测试。生产 lifespan **不再** seed 任何节点（空库），需要预置节点
的用例在这里**显式** seed（见 ``_seed_helper``）。
"""

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import ControlServerConfig
from app.db.base import Base
from app.main import create_app

import app.db.models  # noqa: F401 - 导入即把全部模型注册到 Base.metadata（drop/create 用）
from ._seed_helper import seed_test_db

# 设了即整套测试改用此 PostgreSQL DSN（CI 的 PG job 用）；未设走 SQLite 每用例独立文件。
_PG_TEST_URL = os.environ.get("DN42_CONTROL_TEST_DATABASE_URL")


@pytest.fixture
def config(tmp_path: Path) -> ControlServerConfig:
    url = _PG_TEST_URL or f"sqlite+aiosqlite:///{(tmp_path / 'control.db').as_posix()}"
    return ControlServerConfig(
        database_url=url,
        seed_bootstrap_node=True,
        admin_token="test-admin-token",
    )


@pytest.fixture(autouse=True)
def _reset_pg_schema(config: ControlServerConfig) -> Iterator[None]:
    """PG 共享库：每用例前 drop+create 全表清场（SQLite 每用例独立文件，无需）。"""

    if config.database_url.startswith("sqlite"):
        yield
        return

    async def _reset() -> None:
        engine = create_async_engine(config.database_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_reset())
    yield


@pytest.fixture
def client(config: ControlServerConfig) -> Iterator[TestClient]:
    # 生产路径不 seed；测试在这里显式预置 bootstrap 节点。
    if config.seed_bootstrap_node:
        seed_test_db(config)
    # 默认携带 admin Bearer：admin API 现在 fail-closed，绝大多数用例直接复用；
    # agent 面的鉴权用例显式覆盖 Authorization，不受默认头影响。
    app = create_app(config)
    with TestClient(
        app, headers={"Authorization": f"Bearer {config.admin_token}"}
    ) as client:
        yield client
