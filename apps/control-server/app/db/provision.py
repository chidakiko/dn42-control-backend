from __future__ import annotations

"""把一个完整 ``DesiredState`` 落库为「节点 + 子表 + 世代快照」。

seed 与 admin provision 端点共用同一套拆解逻辑：

- ``Node.base_template`` = DesiredState 去掉子表(interfaces / bgp_sessions)与
  ``generation`` 之后的部分；DNS 保留骨架但把 ``zones`` 抽到 ``DnsZone`` 行。
- ``WgInterface`` / ``BgpSession`` / ``DnsZone`` 子表逐行写入。
- 调用 ``materialize`` 产出新一代 snapshot，并把 ``Node.current_generation`` 指过去。

``provision_node_from_state`` 是幂等的：节点已存在时会覆盖 base_template、
重建子表，再 materialize 一代新的 snapshot——因此重复部署（例如 compose 里的
provisioner 重跑）不会报错也不会留下脏数据。
"""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from dn42_schemas import DesiredState

from ..services.materializer import materialize
from ..services.tokens import hash_token, literal_token_id
from .models import AgentToken, BgpSession, DnsZone, Node, WgInterface


def _split_base_template(state: DesiredState) -> dict:
    """从 DesiredState dump 中切出 Node.base_template（不含子表 / generation）。"""

    snapshot = state.model_dump(mode="json")
    base_template = {
        key: value
        for key, value in snapshot.items()
        if key not in {"interfaces", "bgp_sessions", "generation"}
    }
    # DNS：保留 bind_addresses / forwards / enabled 等骨架，但 zones 落 DnsZone 表，
    # 由 materializer 注回，避免双写。
    if isinstance(base_template.get("dns"), dict):
        dns_skeleton = dict(base_template["dns"])
        dns_skeleton["zones"] = []
        base_template["dns"] = dns_skeleton
    return base_template


async def _delete_children(session: AsyncSession, node_id: str) -> None:
    for model in (WgInterface, BgpSession, DnsZone):
        await session.execute(delete(model).where(model.node_id == node_id))


def _add_children(session: AsyncSession, state: DesiredState) -> None:
    node_id = state.node.node_id
    for order, interface in enumerate(state.interfaces):
        session.add(
            WgInterface(
                node_id=node_id,
                peering_id=None,
                name=interface.name,
                kind=interface.kind.value,
                enabled=True,
                spec=interface.model_dump(mode="json"),
                sort_order=order,
            )
        )
    for order, sess in enumerate(state.bgp_sessions):
        session.add(
            BgpSession(
                node_id=node_id,
                peering_id=None,
                name=sess.name,
                remote_asn=sess.remote_asn,
                enabled=sess.enabled,
                spec=sess.model_dump(mode="json"),
                sort_order=order,
            )
        )
    if state.dns is not None:
        for zone in state.dns.zones:
            session.add(
                DnsZone(
                    node_id=node_id,
                    name=zone.zone,
                    spec=zone.model_dump(mode="json"),
                    enabled=True,
                )
            )


async def provision_node_from_state(
    session: AsyncSession,
    state: DesiredState,
    *,
    agent_token: str | None = None,
    reason: str | None = None,
) -> DesiredState:
    """把整份 DesiredState 写入控制面，返回 materialize 后的最新一代。

    Args:
        session: 进行中的 AsyncSession（调用方负责 commit）。
        state: 完整 DesiredState；其 ``node.node_id`` 作为主键。
        agent_token: 若给定，确保该 token 绑定到此节点（已存在则跳过）。
        reason: 写入 Generation 的原因备注。

    幂等：节点已存在时覆盖 base_template + 重建子表 + 重新 materialize。
    """

    node_id = state.node.node_id
    base_template = _split_base_template(state)

    node = await session.get(Node, node_id)
    if node is None:
        node = Node(
            node_id=node_id,
            site=state.node.site,
            asn=state.node.asn,
            router_id=state.node.router_id,
            loopback_ipv4=state.node.loopback_ipv4,
            loopback_ipv6=state.node.loopback_ipv6,
            ipv4_prefixes=list(state.node.ipv4_prefixes),
            ipv6_prefixes=list(state.node.ipv6_prefixes),
            inventory={},
            labels={},
            base_template=base_template,
            current_generation=0,
        )
        session.add(node)
    else:
        node.site = state.node.site
        node.asn = state.node.asn
        node.router_id = state.node.router_id
        node.loopback_ipv4 = state.node.loopback_ipv4
        node.loopback_ipv6 = state.node.loopback_ipv6
        node.ipv4_prefixes = list(state.node.ipv4_prefixes)
        node.ipv6_prefixes = list(state.node.ipv6_prefixes)
        node.base_template = base_template
        await _delete_children(session, node_id)
        await session.flush()

    _add_children(session, state)

    if agent_token is not None:
        # 与 TokenStore 同一套哈希模型：明文不落库，主键为确定性派生 id。
        token_id = literal_token_id(agent_token)
        existing = await session.get(AgentToken, token_id)
        if existing is None:
            session.add(
                AgentToken(
                    token=token_id,
                    token_hash=hash_token(agent_token),
                    node_id=node_id,
                    agent_id=f"{node_id}-agent",
                )
            )

    await session.flush()
    desired = await materialize(session, node_id, reason=reason or "provision")
    assert desired is not None, "provision materialization must succeed"
    return desired


__all__ = ["provision_node_from_state"]
