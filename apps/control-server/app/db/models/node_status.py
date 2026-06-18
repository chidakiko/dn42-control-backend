from __future__ import annotations

"""节点运行时健康 ORM：``node_status`` + ``node_status_events``。

控制面以前只是丢弃 agent 上报的 snapshot / report / apply-result（MVP 桩）。
这两张表把上报持久化下来：

- ``node_status``：每个节点一行，保存"最近一次"的 snapshot / report / apply 摘要，
  外加派生出来的 ``health``（ok / degraded / stale / unknown）。供健康面板直接读。
- ``node_status_events``：append-only 历史，按 ``kind`` 区分 snapshot / report /
  apply；用于排障回溯。写入时按节点+种类裁剪到最近 ``_HISTORY_KEEP`` 条，避免无界增长。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NodeStatus(Base):
    """每个节点的最新运行时健康。``node_id`` 主键，随上报 upsert。"""

    __tablename__ = "node_status"

    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), primary_key=True
    )

    # 控制面已发布的期望世代 / agent 实际观测到的世代。
    desired_generation: Mapped[int | None] = mapped_column(Integer)
    observed_generation: Mapped[int | None] = mapped_column(Integer)

    # 最近一次对账状态（ApplyStatus 字面量：succeeded / degraded / failed / skipped）。
    last_report_status: Mapped[str | None] = mapped_column(String(32))
    last_apply_status: Mapped[str | None] = mapped_column(String(32))

    drift_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 派生健康：ok / degraded / stale / unknown。
    health: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)

    # 最近一次完整 payload（便于面板下钻，无需再查历史表）。
    last_snapshot: Mapped[dict | None] = mapped_column(JSON)
    last_report: Mapped[dict | None] = mapped_column(JSON)
    last_apply: Mapped[dict | None] = mapped_column(JSON)

    last_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_report_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_apply_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


class NodeStatusEvent(Base):
    """append-only 上报历史。``kind`` ∈ {snapshot, report, apply}。"""

    __tablename__ = "node_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    generation: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now(), index=True
    )


__all__ = ["NodeStatus", "NodeStatusEvent"]
