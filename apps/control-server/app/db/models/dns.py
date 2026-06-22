from __future__ import annotations

"""DNS 组 ORM——记录为中心的**共享** DNS（不再按节点级保存）。

三级模型：

- ``DnsGroup``：谁来提供 DNS + 在哪些 IP 上（``name`` + ``bind_addresses``）。节点经
  ``Node.dns_group_id`` 订阅；多个节点订阅同一组 ⇒ 相同配置 ⇒ anycast / 任拨。
- ``DnsGroupZone``：组声明的**权威 zone**（正向如 ``example.dn42``、反向如
  ``20.172.in-addr.arpa``）+ 可选 SOA 覆盖（主 NS / 管理邮箱 / 刷新等，留空即自动生成）。
- ``DnsRecord``：扁平记录（``name`` / ``type`` / ``content`` / ``ttl`` / ``comment``），FK 到
  zone。rDNS 就是反向 zone 下 ``type=PTR`` 的记录。``comment`` 仅供 WebUI，不进 zone 文件。

materializer 把 组→zone→记录 组装成 ``DnsSpec``（每 zone 内联 records + 自动 SOA）。
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DnsGroup(Base):
    """共享 DNS 组：一份可被多节点订阅的 DNS 配置（zone 落 ``DnsGroupZone``）。"""

    __tablename__ = "dns_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # 提供 DNS 服务的 IP（anycast 节点据此拿到一致的监听地址）。
    bind_addresses: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    # 转发（递归 resolver）配置；纯权威场景留空即可。
    forwards: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )

    zones: Mapped[list["DnsGroupZone"]] = relationship(
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="select",
    )


class DnsGroupZone(Base):
    """组声明的权威 zone + 可选 SOA 覆盖。同一组内 ``zone`` 唯一。

    SOA 字段全部可空：留空时 materializer 自动生成（主 NS=``ns.<zone>``、管理邮箱=
    ``hostmaster.<zone>``、序列号=generation、刷新/重试/过期/最小用合理默认）。
    """

    __tablename__ = "dns_group_zones"
    __table_args__ = (
        UniqueConstraint("dns_group_id", "zone", name="uq_dns_group_zones_group_id_zone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dns_group_id: Mapped[int] = mapped_column(
        ForeignKey("dns_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    zone: Mapped[str] = mapped_column(String(255), nullable=False)

    # SOA 覆盖（留空=自动）。
    primary_ns: Mapped[str | None] = mapped_column(String(255))
    admin_email: Mapped[str | None] = mapped_column(String(255))
    soa_refresh: Mapped[int | None] = mapped_column(Integer)
    soa_retry: Mapped[int | None] = mapped_column(Integer)
    soa_expire: Mapped[int | None] = mapped_column(Integer)
    soa_minimum: Mapped[int | None] = mapped_column(Integer)
    default_ttl: Mapped[int | None] = mapped_column(Integer)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )

    group: Mapped[DnsGroup] = relationship(back_populates="zones", lazy="joined")
    records: Mapped[list["DnsRecord"]] = relationship(
        back_populates="zone_ref",
        cascade="all, delete-orphan",
        lazy="select",
    )


class DnsRecord(Base):
    """一条 DNS 资源记录（你的「记录表」）。``name`` 是 zone 内的主机名（相对 / ``@`` / FQDN）。"""

    __tablename__ = "dns_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dns_group_zone_id: Mapped[int] = mapped_column(
        ForeignKey("dns_group_zones.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    ttl: Mapped[int | None] = mapped_column(Integer)
    # 仅供 WebUI 给人看，不进 zone 文件。
    comment: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )

    zone_ref: Mapped[DnsGroupZone] = relationship(back_populates="records", lazy="joined")


__all__ = ["DnsGroup", "DnsGroupZone", "DnsRecord"]
