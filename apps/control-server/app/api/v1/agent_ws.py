from __future__ import annotations

"""Agent ←→ Control Server 实时事件通道。

握手鉴权：复用 HTTP 的 `Authorization: Bearer <agent_token>` 头；解析失败一律
以 `4401` 关闭连接（WS 关闭码区间 4xxx 是给应用层用的）。

通道隔离：路径 `/agent/ws/{node_id}` 带上目标节点。服务端会校验
`token 解析出的 node_id == 路径里的 node_id`，不一致以 `4403` 关闭。这样多个
agent 各自连自己的 `/ws/<node>`，既能在 URL 层面看出"私聊"对象，又能防止某个
节点的 token 被误用到别的通道。

数据契约：
- 连接成功后立即下发一条 `hello` 事件；
- 之后只下发 `desired_state_updated` / `snapshot_request` 等"门铃事件"，
  agent 收到后回到 HTTP 拉取真实业务数据。
"""

import asyncio
import contextlib
import logging

from fastapi import APIRouter, Depends, Header, WebSocket, WebSocketDisconnect

from ...core.events import EventBus
from ...schemas.events import HelloEvent
from ...services.desired_state import DesiredStateStore
from ...services.tokens import TokenStore
from ..deps import (
    get_desired_state_ws,
    get_event_bus_ws,
    get_tokens_ws,
    parse_ws_bearer,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

_WS_UNAUTHORIZED = 4401
_WS_FORBIDDEN = 4403


@router.websocket("/ws/{node_id}")
async def agent_ws_for_node(
    websocket: WebSocket,
    node_id: str,
    authorization: str | None = Header(default=None),
    tokens: TokenStore = Depends(get_tokens_ws),
    bus: EventBus = Depends(get_event_bus_ws),
    desired_state: DesiredStateStore = Depends(get_desired_state_ws),
) -> None:
    """节点私有通道：URL 自带目标节点，token 的 node 必须与之一致。"""

    await _serve_agent_ws(
        websocket,
        expected_node_id=node_id,
        authorization=authorization,
        tokens=tokens,
        bus=bus,
        desired_state=desired_state,
    )


async def _serve_agent_ws(
    websocket: WebSocket,
    *,
    expected_node_id: str,
    authorization: str | None,
    tokens: TokenStore,
    bus: EventBus,
    desired_state: DesiredStateStore,
) -> None:
    token = parse_ws_bearer(authorization)
    principal = await tokens.resolve(token) if token else None
    if principal is None:
        await websocket.close(code=_WS_UNAUTHORIZED)
        return

    # 通道隔离：token 的 node 必须与 URL 中的目标节点一致，避免"串台"。
    if principal.node_id != expected_node_id:
        logger.warning(
            "agent ws node mismatch: token node=%s path node=%s",
            principal.node_id,
            expected_node_id,
        )
        await websocket.close(code=_WS_FORBIDDEN)
        return

    await websocket.accept()
    queue = await bus.subscribe(principal.node_id)
    try:
        state = await desired_state.get(principal.node_id)
        hello = HelloEvent(
            node_id=principal.node_id,
            generation=state.generation if state else None,
        )
        await websocket.send_json(hello.model_dump(mode="json"))
        await _pump_events(websocket, queue)
    except WebSocketDisconnect:
        logger.debug("agent ws disconnected: node=%s", principal.node_id)
    finally:
        await bus.unsubscribe(principal.node_id, queue)


async def _pump_events(
    websocket: WebSocket,
    queue: asyncio.Queue[dict],
) -> None:
    """并发跑 reader + writer，任一结束即整体收尾。"""

    async def reader() -> None:
        while True:
            # 只用于感知 agent 断连；当前不接受 agent 通过 WS 推业务数据。
            await websocket.receive_text()

    async def writer() -> None:
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())
    try:
        await asyncio.wait(
            {reader_task, writer_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        for task in (reader_task, writer_task):
            if not task.done():
                task.cancel()
        for task in (reader_task, writer_task):
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, Exception):
                await task


__all__ = ["router"]
