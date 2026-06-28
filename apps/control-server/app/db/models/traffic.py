from __future__ import annotations

"""节点 WG 流量 5min 降采样存档 ORM：``node_traffic_rollup``。

agent 30s 轻量循环上报的 ``WireGuardTrafficSample`` 主要落进 Redis 热窗口（2h、30s
分辨率，供「实时吞吐」视图），但 Redis 是旁路、可丢。这张小表是它的**持久化存档**：
控制面每收到一次采样，按 5min 桶把差分出的瞬时速率累加进对应 bucket，读时取均值。
Redis 失效 / 重启后，``/traffic`` 仍能从这里画出 5min 粒度的历史，不必回头扒快照。

每节点每 bucket 一行（``(node_id, bucket_start)`` 复合主键）：``rx_rate_sum`` /
``tx_rate_sum`` 是落进该桶的各次瞬时速率之和，``sample_count`` 是次数，均值 = 和 / 次数。
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NodeTrafficRollup(Base):
    """每节点每 5min 桶的 WG 吞吐速率降采样存档。随采样 upsert（累加进桶）。"""

    __tablename__ = "node_traffic_rollup"

    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), primary_key=True
    )
    # 桶起点（epoch 秒，对齐到 5min）。复合主键的一部分。
    bucket_start: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # 落进该桶的各次瞬时速率（字节/秒）之和 + 次数；读时 sum/count 得均值。
    rx_rate_sum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    tx_rate_sum: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


__all__ = ["NodeTrafficRollup"]
