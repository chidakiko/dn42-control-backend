from __future__ import annotations

"""管理面整节点 provision 端点。

与逐资源 CRUD 不同，``POST /admin/provision`` 接受一份**完整 DesiredState**，
一次性把「节点 + 接口 + BGP 会话 + DNS 区」落库并发布为新一代。适合：

- 部署期批量灌入多节点（compose 里的 provisioner 容器）；
- 从离线渲染好的 DesiredState 直接导入控制面。

幂等：同一 ``node_id`` 重复 provision 会覆盖旧状态并 materialize 新一代，
不会报 409。可选 ``agent_token`` 让该节点的 agent 能立即用固定 token 注册联调。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from dn42_schemas import DesiredState

from ....core.events import EventBus
from ....db.engine import Database
from ....db.provision import provision_node_from_state
from ....schemas.events import DesiredStateUpdatedEvent
from ...deps import get_database, get_event_bus

router = APIRouter()


class ProvisionIn(BaseModel):
    """provision 请求体：一份完整 DesiredState + 可选固定 agent token。"""

    model_config = ConfigDict(extra="forbid")

    state: dict[str, Any] = Field(description="完整 DesiredState 的 JSON dump")
    agent_token: str | None = Field(
        default=None, description="可选：绑定到该节点的固定 agent token"
    )


class ProvisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    generation: int
    subscribers: int
    delivered: int


@router.post("/provision", response_model=ProvisionOut, status_code=status.HTTP_201_CREATED)
async def provision_node(
    payload: ProvisionIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> ProvisionOut:
    try:
        state = DesiredState.model_validate(payload.state)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "invalid DesiredState", "errors": exc.errors(include_url=False)},
        ) from exc

    node_id = state.node.node_id
    async with db.session() as session:
        desired = await provision_node_from_state(
            session,
            state,
            agent_token=payload.agent_token,
            reason="admin provision",
        )

    # 通知已连接该节点私有通道的 agent：去重新 GET 全量 DesiredState。
    event = DesiredStateUpdatedEvent(generation=desired.generation, reason="admin provision")
    delivered = await bus.publish(node_id, event.model_dump(mode="json"))
    return ProvisionOut(
        node_id=node_id,
        generation=desired.generation,
        subscribers=bus.subscriber_count(node_id),
        delivered=delivered,
    )


__all__ = ["router"]
