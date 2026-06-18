from __future__ import annotations

"""DNS 区域 ORM。``spec`` 列保存完整 ``DnsZoneSpec`` dump，由 materializer 反序列化。"""

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


class DnsZone(Base):
    """节点级 DNS 区域。同一节点内 ``name`` 唯一。"""

    __tablename__ = "dns_zones"
    __table_args__ = (
        UniqueConstraint("node_id", "name", name="uq_dns_zones_node_id_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    spec: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )

    node: Mapped[Node] = relationship(back_populates="dns_zones", lazy="joined")


__all__ = ["DnsZone"]
