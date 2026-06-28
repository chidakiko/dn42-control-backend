from __future__ import annotations

"""节点 WG 流量仓库 ``TrafficStore``——30s 高分辨率吞吐时间线的读写层。

agent 30s 轻量循环上报 ``WireGuardTrafficSample``（全 peer 累计收 / 发字节之和），
这里做两件事：

1. **Redis 热窗口**：把原始累计采样压进 ``traffic:window:<node>`` 列表（最近 ~2h、
   240 条、最新在头部），读时取回相邻差分出 30s 粒度速率——「实时吞吐」视图的主源。
2. **PG 5min 降采样存档**：每次采样顺手把对相邻采样差分出的瞬时速率累加进
   ``node_traffic_rollup`` 的 5min 桶（``rx_rate_sum`` / ``sample_count``）。Redis 是
   旁路、可丢；存档让 Redis 失效 / 重启后 ``/traffic`` 仍能画 5min 粒度历史。

读取优先级（见 :meth:`node_series`）：Redis 热窗口（30s）→ PG 存档（5min）→ 空。
端点据空回落到 ``compute_node_traffic`` 的快照差分（~5min）。绝不参与对账 / apply。
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select

from dn42_schemas import WireGuardTrafficSample

from ..db.engine import Database
from ..db.models import NodeTrafficRollup
from .cache import Cache
from .observability import traffic_series_from_samples

# 热窗口：30s 采样 × 240 ≈ 2h；TTL 略大于窗口跨度，节点停报后自动过期。
_HOT_CAP = 240
_HOT_TTL = 7200
# 5min 降采样桶 + 每节点最多保留多少桶（288 × 5min = 24h 存档）。
_BUCKET_S = 300
_ROLLUP_KEEP = 288


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _rate(prev: dict | None, cur: dict) -> tuple[float, float] | None:
    """相邻两次累计采样差分出瞬时速率（字节/秒）；无前样 / Δt≤0 → None（跳过）。"""

    if prev is None:
        return None
    a, b = _parse(prev.get("captured_at")), _parse(cur.get("captured_at"))
    if a is None or b is None:
        return None
    dt = (b - a).total_seconds()
    if dt <= 0:
        return None
    rx = max(0, (cur.get("rx_bytes") or 0) - (prev.get("rx_bytes") or 0)) / dt
    tx = max(0, (cur.get("tx_bytes") or 0) - (prev.get("tx_bytes") or 0)) / dt
    return rx, tx


class TrafficStore:
    """读写 WG 流量热窗口（Redis）+ 5min 降采样存档（PG）。各方法各自开 session。"""

    def __init__(self, database: Database, *, cache: "Cache | None" = None) -> None:
        self._db = database
        self._cache = cache or Cache(None)

    @staticmethod
    def _window_key(node_id: str) -> str:
        return f"traffic:window:{node_id}"

    async def record_sample(self, sample: WireGuardTrafficSample) -> None:
        """登记一次轻量采样：压入 Redis 热窗口 + 把瞬时速率累加进 PG 5min 存档桶。"""

        key = self._window_key(sample.node_id)
        # 取窗口现状（最新在头部）：头元素即「上一次采样」，用于差分速率 + 喂存档。
        window = await self._cache.list_range_json(key)
        prev = window[0] if window else None
        entry = {
            "captured_at": sample.captured_at,
            "rx_bytes": sample.rx_bytes,
            "tx_bytes": sample.tx_bytes,
        }
        await self._cache.list_push_capped(key, entry, cap=_HOT_CAP, ttl_seconds=_HOT_TTL)

        rate = _rate(prev, entry)
        if rate is not None:
            await self._record_rollup(sample.node_id, sample.captured_at, rate)

    async def _record_rollup(
        self, node_id: str, captured_at: str, rate: tuple[float, float]
    ) -> None:
        ts = _parse(captured_at)
        if ts is None:
            return
        bucket = int(ts.timestamp()) // _BUCKET_S * _BUCKET_S
        rx, tx = rate
        async with self._db.session() as session:
            row = await session.get(NodeTrafficRollup, (node_id, bucket))
            if row is None:
                row = NodeTrafficRollup(node_id=node_id, bucket_start=bucket)
                session.add(row)
            row.rx_rate_sum = (row.rx_rate_sum or 0.0) + rx
            row.tx_rate_sum = (row.tx_rate_sum or 0.0) + tx
            row.sample_count = (row.sample_count or 0) + 1
            await self._trim_rollup(session, node_id)

    async def _trim_rollup(self, session, node_id: str) -> None:
        keep = (
            select(NodeTrafficRollup.bucket_start)
            .where(NodeTrafficRollup.node_id == node_id)
            .order_by(NodeTrafficRollup.bucket_start.desc())
            .limit(_ROLLUP_KEEP)
        )
        await session.execute(
            delete(NodeTrafficRollup).where(
                NodeTrafficRollup.node_id == node_id,
                NodeTrafficRollup.bucket_start.notin_(keep),
            )
        )

    async def node_series(self, node_id: str) -> list[dict]:
        """单节点吞吐时间线：Redis 热窗口（30s）优先，回落 PG 5min 存档，再空则 ``[]``。

        返回与 ``compute_node_traffic`` 同结构的速率点（升序）。返回空列表时端点据此
        进一步回落到快照差分，三层降级互不丢数据。
        """

        window = await self._cache.list_range_json(self._window_key(node_id))
        if len(window) >= 2:
            return traffic_series_from_samples(window)
        return await self._rollup_series(node_id)

    async def _rollup_series(self, node_id: str) -> list[dict]:
        async with self._db.session() as session:
            rows = await session.execute(
                select(NodeTrafficRollup)
                .where(NodeTrafficRollup.node_id == node_id)
                .order_by(NodeTrafficRollup.bucket_start)
            )
            out: list[dict] = []
            for row in rows.scalars():
                if not row.sample_count:
                    continue
                out.append(
                    {
                        "captured_at": datetime.fromtimestamp(
                            row.bucket_start, tz=timezone.utc
                        ).isoformat(),
                        "rx_bytes_per_sec": row.rx_rate_sum / row.sample_count,
                        "tx_bytes_per_sec": row.tx_rate_sum / row.sample_count,
                    }
                )
            return out


__all__ = ["TrafficStore"]
