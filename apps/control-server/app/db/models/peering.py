from __future__ import annotations

"""对等关系与底层网络资源 ORM 模型。

设计要点：
- ``Peering`` 是面向运维的"一条对等关系"：可对应 0..1 条 wg + 0..N 条 BGP。
- ``WgInterface`` / ``BgpSession`` 都采用"索引列 + ``spec`` JSON"双层结构：
  少量字段(name / node_id / peering_id / enabled / kind / remote_asn)做索引/查询/约束，
  完整的 Pydantic schema dump 放在 ``spec`` 列，
  保证后端不必随 schema 演进改表。
- 节点级唯一约束 ``UNIQUE(node_id, name)`` 保证不会出现两条同名 wg / BGP。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
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


class Peering(Base):
    """一条对等关系：本地节点对外（或对内部节点）的逻辑连接。"""

    __tablename__ = "peerings"
    __table_args__ = (
        UniqueConstraint("local_node_id", "name", name="uq_peerings_local_node_id_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    local_node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    remote_node_id: Mapped[str | None] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_asn: Mapped[int] = mapped_column(Integer, nullable=False)
    remote_label: Mapped[str | None] = mapped_column(String(128))
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(512))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )

    local_node: Mapped[Node] = relationship(
        back_populates="peerings",
        foreign_keys=[local_node_id],
        lazy="joined",
    )
    remote_node: Mapped[Node | None] = relationship(
        foreign_keys=[remote_node_id],
        lazy="joined",
    )
    wg_interfaces: Mapped[list["WgInterface"]] = relationship(
        back_populates="peering",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    bgp_sessions: Mapped[list["BgpSession"]] = relationship(
        back_populates="peering",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class WgInterface(Base):
    """节点上的接口资源（WireGuard / GRE / dummy / etc.）。

    ``peering_id`` 为空表示纯节点级接口（dummy lo、IGP 隧道等）。
    ``spec`` 列保存完整 ``InterfaceSpec`` 的 Pydantic dump，由 materializer 反序列化使用。
    """

    __tablename__ = "wg_interfaces"
    __table_args__ = (
        UniqueConstraint("node_id", "name", name="uq_wg_interfaces_node_id_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    peering_id: Mapped[int | None] = mapped_column(
        ForeignKey("peerings.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    spec: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    node: Mapped[Node] = relationship(back_populates="wg_interfaces", lazy="joined")
    peering: Mapped[Peering | None] = relationship(back_populates="wg_interfaces", lazy="joined")


class BgpSession(Base):
    """节点上的一条 BGP 会话。``spec`` 列保存完整 ``BgpSessionSpec`` dump。"""

    __tablename__ = "bgp_sessions"
    __table_args__ = (
        UniqueConstraint("node_id", "name", name="uq_bgp_sessions_node_id_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    peering_id: Mapped[int | None] = mapped_column(
        ForeignKey("peerings.id", ondelete="SET NULL"), index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    remote_asn: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    spec: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    node: Mapped[Node] = relationship(back_populates="bgp_sessions", lazy="joined")
    peering: Mapped[Peering | None] = relationship(back_populates="bgp_sessions", lazy="joined")


__all__ = ["BgpSession", "Peering", "WgInterface"]
