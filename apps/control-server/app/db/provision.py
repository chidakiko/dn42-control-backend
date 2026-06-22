from __future__ import annotations

"""把一个完整 ``DesiredState`` 落库为「节点 + 子表 + 世代快照」。

seed 与 admin provision 端点共用同一套拆解逻辑：

- ``Node.base_template`` = DesiredState 去掉子表(interfaces / bgp_sessions)与
  ``generation`` 之后的部分；DNS **整段剥掉**——DNS 已改为共享组模型，节点经
  ``Node.dns_group_id`` 订阅（由 admin DNS 组 API 单独分配），不再随节点 provision 落库。
- ``WgInterface`` / ``BgpSession`` 子表逐行写入。
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
from .models import AgentToken, BgpSession, Node, WgInterface


def _split_base_template(state: DesiredState) -> dict:
    """从 DesiredState dump 中切出 Node.base_template（不含子表 / generation）。"""

    snapshot = state.model_dump(mode="json")
    base_template = {
        key: value
        for key, value in snapshot.items()
        if key not in {"interfaces", "bgp_sessions", "generation"}
    }
    # DNS 整段剥掉：改为共享组模型，节点经 dns_group_id 订阅，不存在 base_template 里。
    base_template["dns"] = None
    # 节点身份字段是 nodes 表列的权威值，materialize 时由 materializer._node_payload 用 DB 列
    # 无条件覆盖。这里把这些被覆盖的字段从 base_template.node 剥掉，避免存一份永远被忽略、
    # 却会在 renumber 时误导人的陈旧副本（单一真相源）。region/site 无对应权威覆盖，保留。
    node_dump = dict(snapshot.get("node") or {})
    for overridden in (
        "node_id",
        "asn",
        "router_id",
        "loopback_ipv4",
        "loopback_ipv6",
        "ipv4_prefixes",
        "ipv6_prefixes",
    ):
        node_dump.pop(overridden, None)
    base_template["node"] = node_dump
    return base_template


async def _delete_children(session: AsyncSession, node_id: str) -> None:
    for model in (WgInterface, BgpSession):
        await session.execute(delete(model).where(model.node_id == node_id))


def _add_children(session: AsyncSession, state: DesiredState) -> None:
    node_id = state.node.node_id
    for order, interface in enumerate(state.interfaces):
        row = WgInterface(node_id=node_id, peering_id=None, enabled=True, sort_order=order)
        row.apply_spec(interface)
        session.add(row)
    for order, sess in enumerate(state.bgp_sessions):
        row = BgpSession(node_id=node_id, peering_id=None, sort_order=order)
        row.apply_spec(sess)
        session.add(row)


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
