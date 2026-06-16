from __future__ import annotations

"""节点路由全表 ORM：``node_routing`` + ``node_routing_events``。

agent 周期上报的 ``RoutingTableSnapshot`` 持久化在这里，与 reconcile 健康
（``node_status``）分开——路由全表是独立的观测维度，体量更大、节奏更慢。

- ``node_routing``：每个节点一行，保存最近一次全表的原始路由 + 预聚合结果
  （起源 AS 分布、前缀长度直方图、AS path 长度、RPKI 计数等），供查询接口
  直接读，无需每次重算。
- ``node_routing_events``：append-only 时间序列，每次全表快照落一条计数 + 相对
  上一次的 churn（新增 / 撤销前缀数），供 Radar 式的趋势图。按节点裁剪到最近
  ``_HISTORY_KEEP`` 条，避免无界增长。
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NodeRouting(Base):
    """每个节点的最新 BIRD 路由全表 + 预聚合。``node_id`` 主键，随上报 upsert。"""

    __tablename__ = "node_routing"

    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), primary_key=True
    )

    # 采集状态（observed / unavailable / not-observed）与采集时刻。
    observation: Mapped[str] = mapped_column(String(16), default="not-observed", nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 表规模（按最优路径去重后的唯一前缀数）。
    route_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    route_count_v4: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    route_count_v6: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # RPKI 分布计数。
    rpki_valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rpki_invalid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rpki_unknown: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rpki_not_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 预聚合（origins / prefix_lengths / as_path_lengths / peers）与原始路由列表。
    aggregates: Mapped[dict | None] = mapped_column(JSON)
    routes: Mapped[list | None] = mapped_column(JSON)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


class NodeRoutingEvent(Base):
    """append-only 路由表时间序列（每次全表快照一条 + churn）。"""

    __tablename__ = "node_routing_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    route_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    route_count_v4: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    route_count_v6: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    rpki_valid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rpki_invalid: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rpki_unknown: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rpki_not_found: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 相对上一次快照的 churn：新增 / 撤销的前缀数。
    announced: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    withdrawn: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now(), index=True
    )


__all__ = ["NodeRouting", "NodeRoutingEvent"]
