from __future__ import annotations

"""控制面 DB 仓库（``TokenStore`` / ``DesiredStateStore``）的异步集成测试。

控制面把 \"agent token\" 与 \"DesiredState 当前 generation\" 同时持久化到 SQL，
任何对接口的语义偏差都会让 agent 误信旧 generation 或被错误踢下线。本文件
锐意锁定以下不变量：

* ``TokenStore``: ``issue`` 写入显式 token、``resolve`` 能根据 token 回到对应
  ``Principal``、``revoke`` 后再次 ``resolve`` 返回 ``None``，三步形成完整生命
  周期闭环。
* ``DesiredStateStore.bump``: 每次显式 bump 都会向 ``Generation`` 表追加一行
  并把 generation 单调递增；``current`` 永远返回最大的 generation。
* 异步 fixture 使用 ``aiosqlite`` 文件后端，确保跑出来的行为与生产 PG 后端
  一致（避免使用 ``in-memory`` 时连接池无法复用同一份数据的常见坑）。
"""

import asyncio

import pytest
from sqlalchemy import select

from app.core.config import ControlServerConfig
from app.db.engine import Database
from app.db.models import Generation, Node
from app.services.desired_state import DesiredStateStore
from app.services.tokens import TokenStore


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(f"sqlite+aiosqlite:///{(tmp_path / 'control.db').as_posix()}")


@pytest.fixture
def config() -> ControlServerConfig:
    return ControlServerConfig(seed_bootstrap_node=True)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.mark.asyncio
async def test_token_store_roundtrip(database: Database) -> None:
    from app.db.base import Base

    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        session.add(Node(node_id="n1", asn=65000, router_id="10.0.0.1"))

    store = TokenStore(database)
    issued = await store.issue("n1", token="t1")
    assert issued == "t1"

    principal = await store.resolve("t1")
    assert principal is not None
    assert principal.node_id == "n1"

    await store.revoke("t1")
    assert await store.resolve("t1") is None

    await database.dispose()


@pytest.mark.asyncio
async def test_desired_state_bump_writes_new_generation(
    database: Database, config: ControlServerConfig
) -> None:
    from app.db.base import Base
    from app.db.seed import seed_initial_data

    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        await seed_initial_data(session, config)

    store = DesiredStateStore(database)
    before = await store.get(config.bootstrap_node_id)
    assert before is not None
    assert before.generation == 1

    bumped = await store.bump(config.bootstrap_node_id, reason="test")
    assert bumped is not None
    assert bumped.generation == 2

    async with database.session() as session:
        rows = (
            await session.execute(
                select(Generation).where(Generation.node_id == config.bootstrap_node_id)
            )
        ).scalars().all()
    assert sorted(r.generation for r in rows) == [1, 2]
    assert any(r.reason == "test" for r in rows)

    await database.dispose()


@pytest.mark.asyncio
async def test_materialize_prunes_old_generations_beyond_retention(
    database: Database, config: ControlServerConfig
) -> None:
    """materialize 的保留窗口裁剪：每节点世代数有界，且总保留当前代。"""

    from app.db.base import Base
    from app.db.seed import seed_initial_data
    from app.services.materializer import materialize

    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        await seed_initial_data(session, config)

    node_id = config.bootstrap_node_id
    # 反复物化，保留窗口设为 3。
    for _ in range(8):
        async with database.session() as session:
            await materialize(session, node_id, reason="churn", keep_generations=3)

    async with database.session() as session:
        rows = (
            await session.execute(
                select(Generation).where(Generation.node_id == node_id)
            )
        ).scalars().all()
        node = await session.get(Node, node_id)

    gens = sorted(r.generation for r in rows)
    # 只保留最近 3 代，且当前代（current_generation）必在其中。
    assert len(gens) == 3
    assert node is not None
    assert node.current_generation in gens
    assert gens == [node.current_generation - 2, node.current_generation - 1, node.current_generation]

    await database.dispose()


@pytest.mark.asyncio
async def test_bump_on_unknown_node_returns_none(database: Database) -> None:
    from app.db.base import Base

    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    store = DesiredStateStore(database)
    assert await store.bump("ghost") is None

    await database.dispose()
