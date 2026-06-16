from __future__ import annotations

"""控制面 → Agent 的实时事件总线。

WS 仅承担"事件门铃"职责：业务数据仍走 HTTP 拉取；这里只把事件投递到对应
node_id 的所有订阅队列。MVP 单进程实现，下一轮接 Redis Pub/Sub 时实现
`publish` / `subscribe` 接口契约保持不变。
"""

import asyncio
from collections import defaultdict
from typing import Any


class EventBus:
    """每个 `node_id` 维护一组订阅者队列。

    - 多个 WS 连接（同一 agent 重连 / 多副本）允许同时订阅同一 node_id。
    - 队列满时丢弃事件，避免单个慢消费者反压控制面；agent 重连后会通过
      HTTP 主动拉取最新世代，不依赖事件流的完整性。
    """

    _MAX_QUEUE = 64

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def subscribe(self, node_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._MAX_QUEUE)
        async with self._lock:
            self._subscribers[node_id].append(queue)
        return queue

    async def unsubscribe(self, node_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            queues = self._subscribers.get(node_id)
            if not queues:
                return
            try:
                queues.remove(queue)
            except ValueError:
                return
            if not queues:
                self._subscribers.pop(node_id, None)

    async def publish(self, node_id: str, event: dict[str, Any]) -> int:
        async with self._lock:
            queues = list(self._subscribers.get(node_id, []))
        delivered = 0
        for queue in queues:
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                continue
        return delivered

    def subscriber_count(self, node_id: str) -> int:
        return len(self._subscribers.get(node_id, []))


__all__ = ["EventBus"]
