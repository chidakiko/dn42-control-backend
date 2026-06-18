from __future__ import annotations

"""节点路由全表 ORM：``node_routing`` + ``node_route_entries`` + ``node_routing_events``。

agent 周期上报的 ``RoutingTableSnapshot`` 持久化在这里，与 reconcile 健康
（``node_status``）分开——路由全表是独立的观测维度，体量更大、节奏更慢。

- ``node_routing``：每个节点一行，保存预聚合结果（起源 AS 分布、前缀长度直方图、
  AS path 长度、RPKI 计数等）+ 表规模计数，供摘要类查询直接读。
- ``node_route_entries``：逐路由明细，**每路由一行**（取代早期把整张表塞进
  ``node_routing.routes`` 单个 JSON 列的做法）。前缀检索走 SQL ``WHERE`` + 索引 +
  ``LIMIT``，不再把数 MB JSON 读进内存全扫；写入按内容哈希门控，稳定期跳过重写。
- ``node_routing_events``：append-only 时间序列，每次全表快照落一条计数 + 相对
  上一次的 churn（新增 / 撤销前缀数），供 Radar 式的趋势图。按节点裁剪到最近
  ``_HISTORY_KEEP`` 条，避免无界增长。
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, func
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

    # 预聚合（origins / prefix_lengths / as_path_lengths / peers / routes_hash）。
    aggregates: Mapped[dict | None] = mapped_column(JSON)
    # 历史遗留：早期把整张表存这里。明细已迁到 ``node_route_entries``，此列恒写 None
    # （保留列免破坏性迁移；存量旧 blob 在下一次上报时被覆盖为 NULL，回收空间）。
    routes: Mapped[list | None] = mapped_column(JSON)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


class NodeRouteEntry(Base):
    """逐路由明细：每路由一行。前缀检索走 SQL + 索引，取代 JSON 全表扫描。

    ``record_snapshot`` 在全表内容变化时整表重写该节点的明细（delete + bulk insert）；
    内容未变（哈希一致）则跳过，避免稳定期反复重写上万行。
    """

    __tablename__ = "node_route_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    prefix: Mapped[str] = mapped_column(String(64), nullable=False)
    # 地址族（按 prefix 是否含冒号预判，建索引供 family 过滤）。
    is_v6: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # 本地起源（static/direct/device，不参与 RPKI），供 scope=local/external 过滤。
    local: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    origin_asn: Mapped[int | None] = mapped_column(Integer)
    protocol: Mapped[str | None] = mapped_column(String(128))
    rpki: Mapped[str | None] = mapped_column(String(16))
    next_hop: Mapped[str | None] = mapped_column(String(128))
    as_path: Mapped[list | None] = mapped_column(JSON)
    communities: Mapped[list | None] = mapped_column(JSON)
    large_communities: Mapped[list | None] = mapped_column(JSON)

    __table_args__ = (
        # 复合索引均以 node_id 打头，兼顾「整节点」与「按族/scope/前缀」过滤；
        # node_id 单列查询由这些索引的最左前缀覆盖。
        Index("ix_node_route_entries_node_v6", "node_id", "is_v6"),
        Index("ix_node_route_entries_node_local", "node_id", "local"),
        Index("ix_node_route_entries_node_prefix", "node_id", "prefix"),
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


__all__ = ["NodeRouteEntry", "NodeRouting", "NodeRoutingEvent"]
