from __future__ import annotations

"""Redis 缓存层：未配置时 no-op、注入 client 时读写/失效、异常静默降级。"""

import pytest

from app.services.cache import Cache


class _FakeRedis:
    """内存假 Redis（async 接口子集）；``boom`` 置真则所有操作抛错，验证降级。"""

    def __init__(self, *, boom: bool = False) -> None:
        self.store: dict[str, str] = {}
        self.boom = boom

    async def get(self, key):
        if self.boom:
            raise RuntimeError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.boom:
            raise RuntimeError("redis down")
        self.store[key] = value

    async def delete(self, *keys):
        if self.boom:
            raise RuntimeError("redis down")
        for k in keys:
            self.store.pop(k, None)


def _cache_with(client) -> Cache:
    c = Cache(None)
    c._client = client  # 注入假 client（绕过真实 redis 构造）
    return c


@pytest.mark.asyncio
async def test_disabled_cache_is_noop() -> None:
    c = Cache(None)
    assert c.enabled is False
    assert await c.get_json("k") is None
    await c.set_json("k", {"a": 1})  # 不抛
    await c.delete("k")  # 不抛
    assert await c.get_json("k") is None  # 仍无缓存


@pytest.mark.asyncio
async def test_set_get_delete_roundtrip() -> None:
    c = _cache_with(_FakeRedis())
    await c.set_json("ds:edge1:5", {"node": {"id": "edge1"}, "n": 5})
    assert await c.get_json("ds:edge1:5") == {"node": {"id": "edge1"}, "n": 5}
    await c.delete("ds:edge1:5")
    assert await c.get_json("ds:edge1:5") is None


@pytest.mark.asyncio
async def test_client_errors_degrade_silently() -> None:
    c = _cache_with(_FakeRedis(boom=True))
    # 读失败 → 视为未命中（None），不抛；写/删失败静默忽略。
    assert await c.get_json("k") is None
    await c.set_json("k", {"x": 1})
    await c.delete("k")
