from __future__ import annotations

"""节点 WireGuard 公钥登记 + 注册一致性校验 + 对端传播。

一节点一把 WG 私钥（所有 peer 共用），所以公钥/托管密文是**节点级**事实，存在
``nodes`` 表。控制面把 ``Node.wireguard_public_key`` 当作权威身份，并：

1. **一致性校验**：首次上报 → 登记（``stored``）；与记录一致 → 放行（``matched``）
   并刷新托管密文；**不一致 → 拒绝（``rejected``）**，端点回 409、事务回滚，节点
   不得用偏离密钥拉隧道。要改密钥只能走显式轮换。
2. **对端传播**：公钥首次登记/变化时，把所有"对端是本节点"的接口（``peering.
   remote_node_id == node_id`` 的 WireGuard 接口）的 ``wireguard_peer.public_key``
   回填为该公钥，并重新物化这些对端节点——内部 peering 双端自动打通。外部 peer
   （``remote_node_id`` 为空）不受影响，其对端公钥保持人工配置值。
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dn42_schemas import InterfaceKind

from ..db.models import Node, Peering, WgInterface
from .materializer import materialize

STATUS_STORED = "stored"
STATUS_MATCHED = "matched"
STATUS_REJECTED = "rejected"
STATUS_UNKNOWN_NODE = "unknown_node"

_WG_KIND = InterfaceKind.WIREGUARD.value


@dataclass(frozen=True, slots=True)
class KeyReportOutcome:
    status: str
    detail: str | None = None
    # 因本次登记被传播 + 重新物化的对端节点 → 新世代号（提交后用于广播）。
    propagated: dict[str, int] = field(default_factory=dict)

    @property
    def rejected(self) -> bool:
        return self.status == STATUS_REJECTED


async def apply_wireguard_key_report(
    session: AsyncSession,
    node_id: str,
    public_key: str,
    private_key_escrow: str | None,
) -> KeyReportOutcome:
    """登记节点公钥并按需传播；返回判定 + 受影响对端节点的新世代。

    ``rejected`` 时调用方应让事务回滚并回 409。传播在同一事务内完成（含对端
    重新物化），调用方在提交后据 ``propagated`` 广播事件。
    """

    node = await session.get(Node, node_id, with_for_update=True)
    if node is None:
        return KeyReportOutcome(STATUS_UNKNOWN_NODE, f"unknown node {node_id}")

    if node.wireguard_public_key is None:
        node.wireguard_public_key = public_key
        node.wireguard_private_key_escrow = private_key_escrow
        status = STATUS_STORED
    elif node.wireguard_public_key == public_key:
        # 稳态：公钥一致即正确恢复/续跑；托管密文可能因换恢复公钥而更新，刷新之。
        if private_key_escrow is not None:
            node.wireguard_private_key_escrow = private_key_escrow
        status = STATUS_MATCHED
    else:
        return KeyReportOutcome(
            STATUS_REJECTED,
            "reported public key does not match the one on record; "
            "restore the original key or perform an explicit rotation",
        )

    # 仅在首次登记时传播（matched 稳态无需重复回填）。
    propagated: dict[str, int] = {}
    if status == STATUS_STORED:
        propagated = await _propagate_to_peers(session, node_id, public_key)

    return KeyReportOutcome(status, propagated=propagated)


async def _propagate_to_peers(
    session: AsyncSession, node_id: str, public_key: str
) -> dict[str, int]:
    """重新物化所有"对端是 node_id"的节点，让它们拉到本节点的新公钥。

    公钥不再回填进对端接口 spec 存一份副本——``materializer`` 会按
    ``peering.remote_node_id`` 现取现填（单一真相源，见 ``_load_peer_public_keys``）。
    这里只负责触发受影响对端重新物化。返回 {对端 node_id: 新世代号}，无内部对端时为空。
    """

    rows = await session.execute(
        select(WgInterface.node_id)
        .join(Peering, WgInterface.peering_id == Peering.id)
        .where(Peering.remote_node_id == node_id, WgInterface.kind == _WG_KIND)
    )
    affected_nodes = {sibling for (sibling,) in rows if sibling != node_id}

    generations: dict[str, int] = {}
    for sibling in sorted(affected_nodes):
        desired = await materialize(
            session, sibling, reason=f"peer {node_id} wireguard public key propagated"
        )
        if desired is not None:
            generations[sibling] = desired.generation
    return generations


__all__ = [
    "KeyReportOutcome",
    "STATUS_MATCHED",
    "STATUS_REJECTED",
    "STATUS_STORED",
    "STATUS_UNKNOWN_NODE",
    "apply_wireguard_key_report",
]
