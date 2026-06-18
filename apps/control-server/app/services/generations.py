from __future__ import annotations

"""世代（generation）读取 / 对比 / 回滚服务。

``generations.snapshot`` 已经保存了每一代完整的 ``DesiredState`` JSON，因此：

- **读取**：直接取出某一代的快照（运维查看历史下发内容）。
- **对比**：纯函数 ``diff_snapshots`` 递归比较两份快照，产出字段级变更列表。
- **回滚**：把目标代的快照作为**新一代**重新发布（generation 号继续单调递增），
  更新 ``Node.current_generation``，由调用方在事务提交后广播事件。

回滚的一个重要语义边界：它只重放 ``generations.snapshot``，**不**回退 normalized
子表（peerings / wg / bgp / dns）。因此回滚后任何会触发 ``materialize`` 的后续
管理写入都会从当前子表重新组装、覆盖这次回滚。回滚是"紧急把已下发内容拨回某一代"
的逃生阀，不是配置编辑——要持久改变，请改子表再走正常 CRUD 流程。
"""

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dn42_schemas import DesiredState

from ..db.models import Generation, Node
from .materializer import DEFAULT_GENERATION_RETENTION


class GenerationNotFoundError(Exception):
    """请求的 ``(node_id, generation)`` 在库中不存在。"""

    def __init__(self, node_id: str, generation: int) -> None:
        super().__init__(f"node {node_id} has no generation {generation}")
        self.node_id = node_id
        self.generation = generation


async def get_generation(
    session: AsyncSession, node_id: str, generation: int
) -> Generation | None:
    """取出某一代的 ORM 行；不存在返回 ``None``。"""

    return await session.scalar(
        select(Generation).where(
            Generation.node_id == node_id,
            Generation.generation == generation,
        )
    )


def diff_snapshots(old: Any, new: Any) -> list[dict[str, Any]]:
    """递归对比两份快照，返回字段级变更列表。

    每条变更形如 ``{"path": "bgp_sessions[0].enabled", "op": "changed",
    "old": true, "new": false}``。``op`` 取值 ``added`` / ``removed`` /
    ``changed``。两份完全一致时返回空列表。
    """

    changes: list[dict[str, Any]] = []
    _diff(old, new, "", changes)
    return changes


def _diff(old: Any, new: Any, path: str, changes: list[dict[str, Any]]) -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new), key=str):
            child = f"{path}.{key}" if path else str(key)
            if key not in old:
                changes.append({"path": child, "op": "added", "old": None, "new": new[key]})
            elif key not in new:
                changes.append({"path": child, "op": "removed", "old": old[key], "new": None})
            else:
                _diff(old[key], new[key], child, changes)
        return

    if isinstance(old, list) and isinstance(new, list):
        for index in range(max(len(old), len(new))):
            child = f"{path}[{index}]"
            if index >= len(old):
                changes.append({"path": child, "op": "added", "old": None, "new": new[index]})
            elif index >= len(new):
                changes.append({"path": child, "op": "removed", "old": old[index], "new": None})
            else:
                _diff(old[index], new[index], child, changes)
        return

    if old != new:
        changes.append({"path": path, "op": "changed", "old": old, "new": new})


async def rollback_to_generation(
    session: AsyncSession,
    node_id: str,
    target_generation: int,
    *,
    reason: str,
    keep_generations: int = DEFAULT_GENERATION_RETENTION,
) -> DesiredState | None:
    """把 ``target_generation`` 的快照重新发布为新一代。

    返回新版 ``DesiredState``。节点不存在返回 ``None``；目标代不存在抛
    ``GenerationNotFoundError``。与 ``materialize`` 同样的串行化纪律：行级
    ``FOR UPDATE`` 锁住节点，保证 generation 严格单调、不撞 UNIQUE。
    """

    node = await session.get(Node, node_id, with_for_update=True)
    if node is None:
        return None

    target = await get_generation(session, node_id, target_generation)
    if target is None:
        raise GenerationNotFoundError(node_id, target_generation)

    new_generation = (node.current_generation or 0) + 1
    # 复用目标快照，只把 generation 号推进到新代——其余内容原样回放。
    snapshot = dict(target.snapshot)
    snapshot["generation"] = new_generation

    # 重新过一遍 schema，拒绝任何历史快照与当前 schema 漂移的情况。
    desired = DesiredState.model_validate(snapshot)
    serialized = desired.model_dump(mode="json")

    session.add(
        Generation(
            node_id=node_id,
            generation=new_generation,
            snapshot=serialized,
            reason=reason,
        )
    )
    node.current_generation = new_generation

    if keep_generations > 0:
        cutoff = new_generation - keep_generations
        if cutoff > 0:
            await session.execute(
                delete(Generation).where(
                    Generation.node_id == node_id,
                    Generation.generation <= cutoff,
                )
            )

    return desired


__all__ = [
    "GenerationNotFoundError",
    "diff_snapshots",
    "get_generation",
    "rollback_to_generation",
]
