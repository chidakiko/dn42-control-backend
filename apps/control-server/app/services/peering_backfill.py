from __future__ import annotations

"""存量配置归并：把节点上的孤儿接口 / BGP 会话总结成 ``Peering`` 行并回填关联。

背景：经整份 DesiredState 导入（``db.provision``）或逐资源 CRUD 建立的
``WgInterface`` / ``BgpSession`` 默认 ``peering_id=None``。本模块按确定性启发式
把它们分组、为每组建一条 ``Peering``、回填子表的 ``peering_id``，让"一条对等关系"
在控制面里有显式的聚合根。

要点：
- ``peering_id`` **不进入 DesiredState**（materializer 不读它），所以回填是纯
  控制面元数据写入：**不 materialize、不广播、不推进世代**。
- 幂等：只处理 ``peering_id IS NULL`` 的行；已纳管的行原样不动，重跑无副作用。
- ``Peering.remote_asn`` 必填且 ``ge=1``：没有任何 BGP 会话的纯传输接口推不出
  ASN，**跳过**（保持孤儿），在结果里记入 ``skipped_interfaces``。
"""

from dataclasses import dataclass, field

from dn42_common import is_address_in_prefix, split_ipv6_zone
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dn42_schemas import BgpSessionSpec, InterfaceKind, InterfaceSpec

from ..db.models import BgpSession, Node, Peering, WgInterface

# 承载对端的接口类型；dummy / loopback 视为节点基础设施，不归并。
_PEER_BEARING_KINDS = {InterfaceKind.WIREGUARD.value, InterfaceKind.UNDERLAY.value}


@dataclass(slots=True)
class PlannedPeering:
    """一组归并计划：将新建的 Peering + 纳管的子资源 id。"""

    name: str
    remote_asn: int
    is_internal: bool
    remote_node_id: str | None
    interface_ids: list[int] = field(default_factory=list)
    bgp_session_ids: list[int] = field(default_factory=list)
    # apply 后回填的真实 Peering 行 id（dry-run 为 None）。
    peering_id: int | None = None


@dataclass(slots=True)
class BackfillResult:
    dry_run: bool
    created: list[PlannedPeering] = field(default_factory=list)
    skipped_interfaces: list[dict[str, object]] = field(default_factory=list)
    skipped_sessions: list[dict[str, object]] = field(default_factory=list)


def _neighbor_address(spec: BgpSessionSpec) -> str:
    """取 BGP neighbor 的纯地址（剥掉 ``%zone``）。"""

    address, _zone = split_ipv6_zone(spec.neighbor)
    return address


def _address_in_any(address: str, prefixes: list[str]) -> bool:
    for prefix in prefixes:
        try:
            if is_address_in_prefix(address, prefix):
                return True
        except ValueError:
            continue
    return False


def _anchor_interface(
    sess_spec: BgpSessionSpec, ifaces: list[tuple[WgInterface, InterfaceSpec]]
) -> WgInterface | None:
    """为一条会话找锚定接口：显式 ``interface`` 名优先，否则按地址归属。"""

    if sess_spec.interface:
        for row, _spec in ifaces:
            if row.name == sess_spec.interface:
                return row

    neighbor = _neighbor_address(sess_spec)
    for row, spec in ifaces:
        candidate_prefixes = list(spec.peer_routes)
        if spec.wireguard_peer is not None:
            candidate_prefixes += list(spec.wireguard_peer.allowed_ips)
        if _address_in_any(neighbor, candidate_prefixes):
            return row
        if _address_in_any(sess_spec.source_address, spec.addresses):
            return row
    return None


async def _resolve_remote_node(
    session: AsyncSession, local_node_id: str, addresses: list[str]
) -> str | None:
    """尽力把 neighbor / source 地址匹配到另一个节点的 loopback / router_id。"""

    candidates = {addr for addr in addresses if addr}
    if not candidates:
        return None
    rows = await session.execute(select(Node).where(Node.node_id != local_node_id))
    for node in rows.scalars():
        node_addrs = {node.loopback_ipv4, node.loopback_ipv6, node.router_id}
        if candidates & {addr for addr in node_addrs if addr}:
            return node.node_id
    return None


def _unique_name(base: str, taken: set[str]) -> str:
    """在已占用名集合里给出不冲突的 peering 名（追加 ``-2/-3…``）。"""

    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


async def backfill_peerings(
    session: AsyncSession, node_id: str, *, dry_run: bool
) -> BackfillResult | None:
    """归并 ``node_id`` 上的孤儿接口 / 会话为 Peering。节点不存在返回 ``None``。

    ``dry_run=True`` 只构造计划、不写库；``False`` 时建 Peering 行并回填 ``peering_id``
    （不 materialize）。
    """

    node = await session.get(Node, node_id)
    if node is None:
        return None

    iface_rows = (
        await session.execute(
            select(WgInterface)
            .where(WgInterface.node_id == node_id, WgInterface.peering_id.is_(None))
            .order_by(WgInterface.sort_order, WgInterface.id)
        )
    ).scalars()
    sess_rows = (
        await session.execute(
            select(BgpSession)
            .where(BgpSession.node_id == node_id, BgpSession.peering_id.is_(None))
            .order_by(BgpSession.sort_order, BgpSession.id)
        )
    ).scalars()

    result = BackfillResult(dry_run=dry_run)

    # 仅"承载对端"的接口参与；解析 spec 失败的行跳过。
    parsed_ifaces: list[tuple[WgInterface, InterfaceSpec]] = []
    for row in iface_rows:
        if row.kind not in _PEER_BEARING_KINDS:
            result.skipped_interfaces.append(
                {"id": row.id, "name": row.name, "reason": f"non-peer-bearing kind {row.kind}"}
            )
            continue
        try:
            parsed_ifaces.append((row, InterfaceSpec.model_validate(row.spec)))
        except ValidationError:
            result.skipped_interfaces.append(
                {"id": row.id, "name": row.name, "reason": "invalid InterfaceSpec"}
            )

    # 每个接口一个候选组；无锚会话按 remote_asn 聚一组。
    iface_groups: dict[int, dict[str, object]] = {
        row.id: {"interface": row, "spec": spec, "sessions": []}
        for row, spec in parsed_ifaces
    }
    asn_groups: dict[int, list[tuple[BgpSession, BgpSessionSpec]]] = {}

    for row in sess_rows:
        try:
            spec = BgpSessionSpec.model_validate(row.spec)
        except ValidationError:
            result.skipped_sessions.append(
                {"id": row.id, "name": row.name, "reason": "invalid BgpSessionSpec"}
            )
            continue
        anchor = _anchor_interface(spec, parsed_ifaces)
        if anchor is not None:
            iface_groups[anchor.id]["sessions"].append((row, spec))  # type: ignore[union-attr]
        else:
            asn_groups.setdefault(spec.remote_asn, []).append((row, spec))

    taken_names = {
        name for (name,) in (
            await session.execute(
                select(Peering.name).where(Peering.local_node_id == node_id)
            )
        )
    }

    planned: list[PlannedPeering] = []

    # 锚定接口组：须至少 1 条会话才能定 ASN，否则跳过该接口。
    for group in iface_groups.values():
        iface_row: WgInterface = group["interface"]  # type: ignore[assignment]
        sessions: list[tuple[BgpSession, BgpSessionSpec]] = group["sessions"]  # type: ignore[assignment]
        if not sessions:
            result.skipped_interfaces.append(
                {
                    "id": iface_row.id,
                    "name": iface_row.name,
                    "reason": "transport-only interface without BGP session (remote_asn unknown)",
                }
            )
            continue
        remote_asn = _dominant_asn([s for _r, s in sessions])
        addresses: list[str] = []
        for _r, s in sessions:
            addresses += [_neighbor_address(s), s.source_address]
        plan = PlannedPeering(
            name=_unique_name(iface_row.name, taken_names),
            remote_asn=remote_asn,
            is_internal=any(s.is_internal(node.asn) for _r, s in sessions),
            remote_node_id=await _resolve_remote_node(session, node_id, addresses),
            interface_ids=[iface_row.id],
            bgp_session_ids=[r.id for r, _s in sessions],
        )
        taken_names.add(plan.name)
        planned.append(plan)

    # 无锚会话：同 remote_asn 聚一条 bgp-only peering。
    for remote_asn, sessions in asn_groups.items():
        addresses = []
        for _r, s in sessions:
            addresses += [_neighbor_address(s), s.source_address]
        plan = PlannedPeering(
            name=_unique_name(f"as{remote_asn}", taken_names),
            remote_asn=remote_asn,
            is_internal=any(s.is_internal(node.asn) for _r, s in sessions),
            remote_node_id=await _resolve_remote_node(session, node_id, addresses),
            interface_ids=[],
            bgp_session_ids=[r.id for r, _s in sessions],
        )
        taken_names.add(plan.name)
        planned.append(plan)

    if not dry_run:
        await _apply(session, node_id, planned)

    result.created = planned
    return result


def _dominant_asn(specs: list[BgpSessionSpec]) -> int:
    """组内 remote_asn 取众数；并列时取首条出现的。"""

    counts: dict[int, int] = {}
    order: list[int] = []
    for spec in specs:
        if spec.remote_asn not in counts:
            order.append(spec.remote_asn)
        counts[spec.remote_asn] = counts.get(spec.remote_asn, 0) + 1
    return max(order, key=lambda asn: (counts[asn], -order.index(asn)))


async def _apply(
    session: AsyncSession, node_id: str, planned: list[PlannedPeering]
) -> None:
    """建 Peering 行并把 ``peering_id`` 回填到纳管的接口 / 会话上。"""

    for plan in planned:
        peering = Peering(
            local_node_id=node_id,
            remote_node_id=plan.remote_node_id,
            name=plan.name,
            remote_asn=plan.remote_asn,
            is_internal=plan.is_internal,
            enabled=True,
            notes="backfilled from existing config",
        )
        session.add(peering)
        await session.flush()
        plan.peering_id = peering.id
        for iface_id in plan.interface_ids:
            iface = await session.get(WgInterface, iface_id)
            if iface is not None:
                iface.peering_id = peering.id
        for sess_id in plan.bgp_session_ids:
            sess = await session.get(BgpSession, sess_id)
            if sess is not None:
                sess.peering_id = peering.id
    await session.flush()


__all__ = ["BackfillResult", "PlannedPeering", "backfill_peerings"]
