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
from ..db.models import Generation, Node
from .materializer import materialize


class DesiredStateStore:
    """每个节点存"已发布的最新 DesiredState"。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def known_node_ids(self) -> list[str]:
        async with self._db.session() as session:
            rows = await session.execute(select(Node.node_id).order_by(Node.node_id))
            return [row[0] for row in rows]

    async def get(self, node_id: str) -> DesiredState | None:
        async with self._db.session() as session:
            node = await session.get(Node, node_id)
            if node is None or node.current_generation == 0:
                return None
            row = await session.execute(
                select(Generation).where(
                    Generation.node_id == node_id,
                    Generation.generation == node.current_generation,
                )
            )
            generation = row.scalar_one_or_none()
            if generation is None:
                return None
            return DesiredState.model_validate(generation.snapshot)

    async def bump(self, node_id: str, *, reason: str | None = None) -> DesiredState | None:
        """从 DB 重算 DesiredState 并发布为新一代。"""

        async with self._db.session() as session:
            return await materialize(session, node_id, reason=reason or "manual bump")


__all__ = ["DesiredStateStore"]
