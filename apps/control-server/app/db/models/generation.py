from __future__ import annotations

"""DesiredState 已发布世代 ORM。

设计：``snapshot`` 字段保存"控制面渲染好"的完整 ``DesiredState`` JSON。
本轮 agent 直接读它；下一轮自动对等器把 normalized 表（peerings / wg /
bgp / dns）物化进同一个 snapshot，对外接口无变化。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .node import Node


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Generation(Base):
    """``(node_id, generation)`` 唯一；最新的一条由 ``Node.current_generation`` 指向。"""

    __tablename__ = "generations"
    __table_args__ = (
        UniqueConstraint("node_id", "generation", name="uq_generations_node_id_generation"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(256))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )

    node: Mapped[Node] = relationship(back_populates="generations", lazy="joined")


__all__ = ["Generation"]
