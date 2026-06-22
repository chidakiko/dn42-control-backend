from __future__ import annotations

"""节点身份与凭证相关 ORM 模型：``nodes`` / ``enrollment_tokens`` / ``agent_tokens``。"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .dns import DnsGroup
    from .generation import Generation
    from .peering import BgpSession, Peering, WgInterface


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Node(Base):
    """单个 DN42 节点。``node_id`` 是节点自身稳定的字符串 ID（如 ``edge1``）。

    ``base_template`` 字段保存"DesiredState 中不来自子表的部分"——即除
    ``generation`` / ``interfaces`` / ``bgp_sessions`` / ``dns`` 之外的所有字段。
    Materializer 用它叠加子表内容生成最终 snapshot。
    """

    __tablename__ = "nodes"

    node_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    site: Mapped[str | None] = mapped_column(String(32))
    # DN42 ASN 是 32 位（4242420000+），超 Postgres int32（≤21.4 亿）→ 必须 BigInteger。
    # SQLite 的 INTEGER 是动态宽度故历史上没暴露，迁 Postgres 时才显形。
    asn: Mapped[int] = mapped_column(BigInteger, nullable=False)
    router_id: Mapped[str] = mapped_column(String(64), nullable=False)
    loopback_ipv4: Mapped[str | None] = mapped_column(String(64))
    loopback_ipv6: Mapped[str | None] = mapped_column(String(64))
    # 节点级 IPv6 link-local（fe80::/10，不带 %zone）。**外部 eBGP** WG 接口的单一真相源：
    # materializer 把 <link_local>/64 派生到这些接口 addresses（见 NodeSpec.link_local /
    # materializer._interface_payload）。内部互联（iBGP/OSPF）用各自 LL，不取本字段。
    link_local: Mapped[str | None] = mapped_column(String(64))
    ipv4_prefixes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    ipv6_prefixes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # 来自 agent 注册的可读元信息。
    inventory: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    labels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)

    # DesiredState 的非子表部分（runtime / bird / templates / schema_version 等）。
    base_template: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)

    # 控制面已发布给该节点的最新世代号。0 表示尚未发布过。
    current_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 生命周期:active(正常)/ decommissioned(退役中)。退役态下 materialize
    # 产出空 interfaces/bgp/dns,节点停止宣告路由、拆除隧道;核心容器保留为惰性
    # (schema 要求 router-netns/wg-gateway/bird-router 必须在)。删节点前必须先退役,
    # 避免直接删库留下仍在宣告路由的孤儿。
    lifecycle: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )

    # 订阅的共享 DNS 组（"分配组即启用 DNS"）。为 NULL ⇒ 该节点不部署 DNS。多个节点指向
    # 同一组 ⇒ 拿到相同 DNS 配置（anycast）。组被删除时置 NULL（节点停跑 DNS，不连带删节点）。
    dns_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("dns_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # 节点级 WireGuard 身份（一节点一把私钥，所有 peer 共用）。派生状态，由 agent
    # 上报、控制面比对/传播。
    # wireguard_public_key: 节点 WG 公钥（自本地私钥推导）。注册一致性校验的权威事实，
    #   并被传播进所有"对端是本节点"的 peer 配置。
    # wireguard_private_key_escrow: 节点 WG 私钥经"恢复公钥"RSA-OAEP 封装的密文；
    #   控制面只存不解，仅离线恢复私钥可解封。
    wireguard_public_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    wireguard_private_key_escrow: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )

    agent_tokens: Mapped[list["AgentToken"]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
        lazy="select",
    )
    peerings: Mapped[list["Peering"]] = relationship(
        back_populates="local_node",
        foreign_keys="Peering.local_node_id",
        cascade="all, delete-orphan",
        lazy="select",
    )
    wg_interfaces: Mapped[list["WgInterface"]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
        lazy="select",
    )
    bgp_sessions: Mapped[list["BgpSession"]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
        lazy="select",
    )
    dns_group: Mapped["DnsGroup | None"] = relationship(lazy="joined")
    generations: Mapped[list["Generation"]] = relationship(
        back_populates="node",
        cascade="all, delete-orphan",
        lazy="select",
        order_by="Generation.generation",
    )


class EnrollmentToken(Base):
    """一次性注册 token。``node_id`` 为空表示尚未指定节点（管理员预生成场景）。

    与 ``AgentToken`` 同安全模型：``token`` 主键是非机密查找键 id（``ent_*``），
    明文 secret 仅在创建响应中出现一次；校验只走 ``token_hash``。
    ``used_at`` 非空表示已被消费（成功换取过 agent token），一次性语义。
    """

    __tablename__ = "enrollment_tokens"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    token_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, index=True
    )
    node_id: Mapped[str | None] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="SET NULL")
    )
    description: Mapped[str | None] = mapped_column(String(256))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )


class AgentToken(Base):
    """长期 Bearer token，绑定到具体 ``node_id``。

    ``token`` 主键是非机密的查找键 id（形如 ``agt_xxxx``），明文 secret 永不落库；
    校验只走 ``token_hash``（完整 Bearer 的 sha256）。``expires_at`` 非空即代表
    该 token 会过期。
    """

    __tablename__ = "agent_tokens"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    node_id: Mapped[str] = mapped_column(
        ForeignKey("nodes.node_id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    node: Mapped[Node] = relationship(back_populates="agent_tokens", lazy="joined")


class PendingRegistration(Base):
    """待审批的 agent 注册请求。

    未知节点（控制面尚未 provision）带合法 enrollment_token 来注册时，不直接发 token，
    而是落一条 pending 记录等管理员 approve / reject。``status`` ∈
    {pending, approved, rejected}。同一 ``requested_node_id`` 重复注册会刷新同一行。
    """

    __tablename__ = "pending_registrations"

    # 同一节点同时**最多一条 pending 行**：防并发注册竞态——PG MVCC 下两个并发 register
    # 都 SELECT-miss 再各插一条 → 重复 pending 行污染审批门；SQLite 单写者把这窗口藏住了。
    # partial unique index（SQLite / PostgreSQL 都支持）从 DB 层兜住；record() 再配
    # IntegrityError 重试走 update 分支。不约束非 pending 行，故 reject 后可重新注册。
    __table_args__ = (
        Index(
            "uq_pending_registrations_node_pending",
            "requested_node_id",
            unique=True,
            sqlite_where=text("status = 'pending'"),
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requested_node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255))
    inventory: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
    )


__all__ = ["AgentToken", "EnrollmentToken", "Node", "PendingRegistration"]
