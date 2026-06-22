from __future__ import annotations

"""DesiredState 的 DB 仓库。

数据流：
- ``get(node_id)`` → 读 ``Node.current_generation`` 指向的 ``Generation.snapshot``。
- ``bump(node_id)`` → 调用 ``materialize``：从 ``Node.base_template`` + 子表
  （wg_interfaces / bgp_sessions / …）重新组装 DesiredState，作为新世代写入。
  这样所有 generation 都是"DB 当前状态的快照"，不存在"复制旧 snapshot"的漂移。
"""

from sqlalchemy import select

from dn42_schemas import DesiredState

from ..db.engine import Database
from .cache import Cache
from ..db.models import Generation, Node
from .materializer import materialize


def _ds_cache_key(node_id: str, generation: int) -> str:
    # 键含 generation（单调递增）→ 天然不可变，新代换键、旧键自然失效，无竞态。
    return f"ds:{node_id}:{generation}"


class DesiredStateStore:
    """每个节点存"已发布的最新 DesiredState"。"""

    def __init__(self, database: Database, *, cache: Cache | None = None) -> None:
        self._db = database
        self._cache = cache or Cache(None)

    async def known_node_ids(self) -> list[str]:
        async with self._db.session() as session:
            rows = await session.execute(select(Node.node_id).order_by(Node.node_id))
            return [row[0] for row in rows]

    async def get(self, node_id: str) -> DesiredState | None:
        async with self._db.session() as session:
            node = await session.get(Node, node_id)
            if node is None or node.current_generation == 0:
                return None
            generation_num = node.current_generation
            # 缓存命中即跳过 Generation 查询（键含 generation，永不返回陈旧快照）。
            cached = await self._cache.get_json(_ds_cache_key(node_id, generation_num))
            if cached is not None:
                return DesiredState.model_validate(cached)
            row = await session.execute(
                select(Generation).where(
                    Generation.node_id == node_id,
                    Generation.generation == generation_num,
                )
            )
            generation = row.scalar_one_or_none()
            if generation is None:
                return None
            await self._cache.set_json(
                _ds_cache_key(node_id, generation_num), generation.snapshot, ttl_seconds=3600
            )
            return DesiredState.model_validate(generation.snapshot)

    async def bump(self, node_id: str, *, reason: str | None = None) -> DesiredState | None:
        """从 DB 重算 DesiredState 并发布为新一代。"""

        async with self._db.session() as session:
            return await materialize(session, node_id, reason=reason or "manual bump")


__all__ = ["DesiredStateStore"]
