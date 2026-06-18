from __future__ import annotations

"""管理面写路径共享逻辑。

写接口的事务纪律（每个写接口都必须遵守）：

1. CRUD 业务变更与 ``materialize_change``（重新物化 DesiredState + 写
   Generation + 推进 ``Node.current_generation``）发生在**同一个事务**里。
   materialize 失败（例如组装出的 spec 不通过 schema 校验）抛出 HTTP 422，
   事务整体回滚——业务表绝不会留下"已写入但不可发布"的数据。
2. 事务提交之后才调用 ``broadcast_change`` 广播 ``desired_state_updated``。
   顺序不能反：事件先发、事务后回滚会让 agent 拉到旧世代。
"""

from typing import Any

import json

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from dn42_schemas import DesiredState

from ....core.events import EventBus
from ....schemas.events import DesiredStateUpdatedEvent
from ....services.materializer import materialize


def _errors_to_json(exc: ValidationError) -> list[dict[str, Any]]:
    """把 ``ValidationError`` 里面可能包含原生 Exception 的 ctx 转成 JSON 安全形式。"""

    return json.loads(exc.json())


async def materialize_change(
    session: AsyncSession,
    node_id: str,
    *,
    reason: str,
) -> DesiredState:
    """在调用方的事务里重新物化指定节点。

    必须在 CRUD 变更 flush 之后、事务提交之前调用；抛出的 HTTPException
    会让 ``Database.session`` 回滚整个事务（含 CRUD 变更本身）。
    """

    try:
        state = await materialize(session, node_id, reason=reason)
    except ValidationError as exc:
        # 物化结果过不了 schema：回滚一切，让管理员看到完整错误。
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "message": "materialization failed schema validation",
                "errors": _errors_to_json(exc),
            },
        ) from exc

    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown node {node_id}",
        )
    return state


async def broadcast_change(
    bus: EventBus,
    node_id: str,
    state: DesiredState,
    *,
    reason: str,
) -> dict[str, Any]:
    """事务提交后广播新世代。返回世代号 + 投递统计。"""

    event = DesiredStateUpdatedEvent(generation=state.generation, reason=reason)
    delivered = await bus.publish(node_id, event.model_dump(mode="json"))
    return {
        "generation": state.generation,
        "subscribers": bus.subscriber_count(node_id),
        "delivered": delivered,
    }


__all__ = ["broadcast_change", "materialize_change", "_errors_to_json"]
