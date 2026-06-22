from __future__ import annotations

"""Redis 缓存层（best-effort，优雅降级）。

控制面的高频读（desired-state 物化结果、fleet/node 健康、routing 聚合）经此缓存，
卸掉重复 DB 查询与 Pydantic 重校验。**缓存永远是旁路**：未配置 Redis、连接失败或
任何异常时，所有操作静默退化为 no-op，调用方照常走 DB——缓存挂了绝不能拖垮控制面。

失效纪律：写操作在 **DB 事务 commit 之后**调用 ``delete`` / ``set``（与 broadcast_change
同纪律），避免"缓存已写但事务回滚"留下脏缓存。desired-state 缓存键含 generation
（单调递增、天然不可变），无并发失效竞态。
"""

import json
import logging
from typing import Any

logger = logging.getLogger("dn42.control.cache")


class Cache:
    """异步 Redis 缓存封装；未配置或不可用时全程 no-op。"""

    def __init__(self, url: str | None) -> None:
        self._url = url
        self._client: Any | None = None
        if not url:
            return
        try:
            import redis.asyncio as redis  # 延迟导入：未装 redis 包也不致命

            self._client = redis.from_url(url, encoding="utf-8", decode_responses=True)
        except Exception:  # noqa: BLE001 - 构造失败即视为无缓存
            logger.warning("cache: Redis 客户端构造失败，缓存禁用", exc_info=True)
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    async def get_json(self, key: str) -> Any | None:
        """读 JSON 值；未命中 / 缓存不可用 / 异常 → None（调用方回落 DB）。"""

        if self._client is None:
            return None
        try:
            raw = await self._client.get(key)
        except Exception:  # noqa: BLE001 - 缓存读失败退化为未命中
            logger.debug("cache: get 失败 key=%s", key, exc_info=True)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

    async def set_json(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> None:
        """写 JSON 值（可选 TTL）；失败静默忽略。"""

        if self._client is None:
            return
        try:
            payload = json.dumps(value, separators=(",", ":"), default=str)
            await self._client.set(key, payload, ex=ttl_seconds)
        except Exception:  # noqa: BLE001 - 缓存写失败不影响主流程
            logger.debug("cache: set 失败 key=%s", key, exc_info=True)

    async def delete(self, *keys: str) -> None:
        """删键（失效）；失败静默忽略。"""

        if self._client is None or not keys:
            return
        try:
            await self._client.delete(*keys)
        except Exception:  # noqa: BLE001
            logger.debug("cache: delete 失败 keys=%s", keys, exc_info=True)

    async def close(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.aclose()
        except Exception:  # noqa: BLE001 - 释放是 best-effort
            pass


__all__ = ["Cache"]
