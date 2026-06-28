from __future__ import annotations

"""WG 流量 30s 采集端到端：ingest → Redis 热窗口 → /traffic（含 rollup 与快照回退）。

测试默认无 Redis（Cache no-op），故需注入一个内存假 Redis 进 ``app.state.traffic`` 才能
走「热窗口 + 5min 存档」主路径；不注入时验证向后兼容的快照差分回退。
"""

from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig
from app.services.cache import Cache
from app.services.traffic import TrafficStore


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class _FakePipe:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def lpush(self, key, value):
        self._ops.append(("lpush", key, value))
        return self

    def ltrim(self, key, start, stop):
        self._ops.append(("ltrim", key, start, stop))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        for op in self._ops:
            if op[0] == "lpush":
                self._redis.lists.setdefault(op[1], []).insert(0, op[2])
            elif op[0] == "ltrim":
                lst = self._redis.lists.get(op[1], [])
                stop = None if op[3] == -1 else op[3] + 1
                self._redis.lists[op[1]] = lst[op[2] : stop]
            # expire: no-op in fake


class _FakeRedis:
    """内存假 Redis：仅实现热窗口用到的 list 子集（pipeline + lrange）。"""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    def pipeline(self) -> _FakePipe:
        return _FakePipe(self)

    async def lrange(self, key, start, stop):
        lst = self.lists.get(key, [])
        end = None if stop == -1 else stop + 1
        return lst[start:end]


def _inject_fake_redis(client: TestClient) -> _FakeRedis:
    """把带内存假 Redis 的 TrafficStore 装进 app.state（取代默认 no-op 缓存）。"""

    fake = _FakeRedis()
    cache = Cache(None)
    cache._client = fake  # 绕过真实 redis 构造
    client.app.state.traffic = TrafficStore(client.app.state.database, cache=cache)
    return fake


def _post_sample(client: TestClient, node: str, token: str, *, ts: str, rx: int, tx: int) -> None:
    sample = {"node_id": node, "captured_at": ts, "rx_bytes": rx, "tx_bytes": tx, "peer_count": 2}
    resp = client.post("/api/v1/agent/wireguard-traffic", headers=_auth(token), json=sample)
    assert resp.status_code == 200, resp.text


# 三个采样：30s 间隔、累计计数线性增长 → 恒定速率 100 B/s（rx）。
_SAMPLES = [
    ("2026-06-27T01:00:00+00:00", 1_000, 500),
    ("2026-06-27T01:00:30+00:00", 4_000, 2_000),
    ("2026-06-27T01:01:00+00:00", 7_000, 3_500),
]


def test_traffic_endpoint_uses_redis_window(
    client: TestClient, config: ControlServerConfig
) -> None:
    node, token = config.bootstrap_node_id, config.bootstrap_agent_token
    _inject_fake_redis(client)
    for ts, rx, tx in _SAMPLES:
        _post_sample(client, node, token, ts=ts, rx=rx, tx=tx)

    body = client.get(f"/api/v1/ui/nodes/{node}/traffic").json()
    pts = body["points"]
    # 3 个采样 → 2 个 30s 区间速率点（高分辨率，非 5min 快照差分）。
    assert len(pts) == 2
    assert pts[0]["rx_bytes_per_sec"] == 100  # (4000-1000)/30
    assert pts[1]["tx_bytes_per_sec"] == 50   # (3500-2000)/30


def test_traffic_rollup_survives_redis_loss(
    client: TestClient, config: ControlServerConfig
) -> None:
    node, token = config.bootstrap_node_id, config.bootstrap_agent_token
    fake = _inject_fake_redis(client)
    for ts, rx, tx in _SAMPLES:
        _post_sample(client, node, token, ts=ts, rx=rx, tx=tx)

    # 模拟 Redis 丢失（flush 热窗口）：/traffic 应回落到 PG 5min 存档,仍出点。
    fake.lists.clear()
    body = client.get(f"/api/v1/ui/nodes/{node}/traffic").json()
    pts = body["points"]
    assert len(pts) == 1  # 三采样的两段速率落进同一 5min 桶 → 一个均值点
    assert pts[0]["rx_bytes_per_sec"] == 100  # 两段均 100 B/s 的均值


def test_traffic_falls_back_to_snapshot_without_redis(
    client: TestClient, config: ControlServerConfig
) -> None:
    """无 Redis（默认 no-op 缓存）+ 无采样：/traffic 回落到快照差分，保持向后兼容。"""

    node, token = config.bootstrap_node_id, config.bootstrap_agent_token

    def _snap(ts: str, rx: int, tx: int) -> dict:
        return {
            "node_id": node,
            "generation": 1,
            "captured_at": ts,
            "containers": [],
            "interfaces": [],
            "wireguard_interfaces": [
                {"name": "wg1", "peer_count": 1, "peers": [
                    {"public_key": "K=", "transfer_rx_bytes": rx, "transfer_tx_bytes": tx}
                ]}
            ],
        }

    for ts, rx, tx in (
        ("2026-06-27T01:00:00+00:00", 1_000_000, 500_000),
        ("2026-06-27T01:05:00+00:00", 31_000_000, 9_500_000),
    ):
        assert client.post(
            "/api/v1/agent/runtime-snapshot", headers=_auth(token), json=_snap(ts, rx, tx)
        ).status_code == 200

    body = client.get(f"/api/v1/ui/nodes/{node}/traffic").json()
    pts = body["points"]
    assert len(pts) == 1  # 两份快照差分一段（5min 分辨率）
    assert pts[0]["rx_bytes_per_sec"] == 30_000_000 / 300


def test_traffic_rejects_other_node(client: TestClient, config: ControlServerConfig) -> None:
    sample = {
        "node_id": "someone-else",
        "captured_at": "2026-06-27T01:00:00+00:00",
        "rx_bytes": 1,
        "tx_bytes": 1,
    }
    resp = client.post(
        "/api/v1/agent/wireguard-traffic",
        headers=_auth(config.bootstrap_agent_token),
        json=sample,
    )
    assert resp.status_code == 403
