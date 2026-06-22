from __future__ import annotations

"""DesiredState materializer：从 normalized 表组装 ``DesiredState`` 并发布新世代。

数据流：
- 读 ``Node.base_template``（DesiredState 中不来自子表的字段，含 ``dns`` 骨架）
- 读该节点所有 ``WgInterface``（按 ``sort_order, id`` 排序）→ ``interfaces``
- 读该节点所有 ``BgpSession``（同上）→ ``bgp_sessions``
- 读该节点所有启用的 ``DnsZone`` → 合并入 ``base_template.dns.zones``；
  ``base_template.dns`` 为空时表示节点不部署 DNS，``DnsZone`` 行被忽略。
- 用 ``dn42_schemas.DesiredState`` 重新校验，保证写入 ``generations.snapshot``
  的内容总是合法 schema
- 写 ``generations`` 新行 + 更新 ``Node.current_generation``
- 返回新版 ``DesiredState`` 供路由层（或事件层）使用

本模块**不**触发 EventBus；由调用方在事务提交后决定是否广播事件，
这样可以避免"事件先发，事务后回滚"导致 agent 拉到旧数据。
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dn42_schemas import DesiredState, InterfaceKind

from ..db.models import BgpSession, DnsGroup, DnsGroupZone, DnsRecord, Node, WgInterface, Generation

# 每个节点保留的最近世代数。每代是一份完整 DesiredState JSON 快照，频繁配置
# 变更下 generations 表会无界增长，超出窗口的旧代在 materialize 时裁剪。
# 当前代由 Node.current_generation 指向，必然落在保留窗口内。
DEFAULT_GENERATION_RETENTION = 100


async def materialize(
    session: AsyncSession,
    node_id: str,
    *,
    reason: str | None = None,
    keep_generations: int = DEFAULT_GENERATION_RETENTION,
) -> DesiredState | None:
    """重新组装 ``node_id`` 的 DesiredState 并写一条新 generation。

    组装 = ``base_template`` 叠加子表（interfaces / bgp_sessions）+ 订阅的 DNS 组，
    并对**单一真相源副本**做现取现填的派生：内部对端接口的 WG 公钥取自对端
    ``Node.wireguard_public_key``（``_load_peer_public_keys`` / ``_interface_payload``），
    不再依赖 spec 里存的副本。

    返回新版 ``DesiredState``；节点不存在返回 ``None``。``keep_generations`` > 0
    时，写入新代后裁剪该节点超出保留窗口的旧世代，防止 generations 表无界增长。
    """

    # 行级锁住节点：并发的 admin 写在 materialize 处串行化，保证 generation
    # 严格单调递增、不会两个事务同读 current_generation 后撞 UNIQUE(node_id,
    # generation)。Postgres/MySQL 走 SELECT ... FOR UPDATE；SQLite 忽略该子句，
    # 但其单写者模型 + UNIQUE 约束兜底（极端并发下后写事务报错回滚，数据不脏）。
    node = await session.get(Node, node_id, with_for_update=True)
    if node is None:
        return None

    new_generation = (node.current_generation or 0) + 1
    wg_rows = await _load_interfaces(session, node_id)
    snapshot = _assemble_snapshot(
        node=node,
        wg_rows=wg_rows,
        bgp_rows=await _load_sessions(session, node_id),
        dns_spec=await _load_dns_group(session, node),
        peer_public_keys=await _load_peer_public_keys(session, wg_rows),
        generation=new_generation,
    )

    # 再用 schema 走一遍以拒绝任何漂移；不通过则直接抛，由上层回滚事务。
    desired = DesiredState.model_validate(snapshot)
    serialized = desired.model_dump(mode="json")

    session.add(
        Generation(
            node_id=node_id,
            generation=new_generation,
            snapshot=serialized,
            reason=reason or "materialize",
        )
    )
    node.current_generation = new_generation

    # 保留窗口裁剪：删掉 generation <= new_generation - keep 的旧代。当前代
    # （= new_generation）必然保留；裁剪只在同一事务内随写入发生，无需后台任务。
    if keep_generations > 0:
        cutoff = new_generation - keep_generations
        if cutoff > 0:
            await session.execute(
                delete(Generation).where(
                    Generation.node_id == node_id,
                    Generation.generation <= cutoff,
                )
            )

    return desired


async def _load_interfaces(session: AsyncSession, node_id: str) -> list[WgInterface]:
    rows = await session.execute(
        select(WgInterface)
        .where(WgInterface.node_id == node_id)
        .order_by(WgInterface.sort_order, WgInterface.id)
    )
    return list(rows.scalars())


async def _load_sessions(session: AsyncSession, node_id: str) -> list[BgpSession]:
    rows = await session.execute(
        select(BgpSession)
        .where(BgpSession.node_id == node_id)
        .order_by(BgpSession.sort_order, BgpSession.id)
    )
    return list(rows.scalars())


async def _load_peer_public_keys(
    session: AsyncSession, wg_rows: list[WgInterface]
) -> dict[str, str]:
    """对「对端是另一受管节点」的接口，取对端节点的权威 WG 公钥（单一真相源）。

    WG 公钥的真相源是对端 ``Node.wireguard_public_key``（agent 上报、控制面校验）。
    内部对端接口的 ``wireguard_peer.public_key`` 不再急切回填进 spec 存一份副本，而是
    materialize 时按 ``peering.remote_node_id`` 现取现填。返回 {remote_node_id: pubkey}，
    只含已登记公钥的对端；未登记 / 外部对端（remote_node_id 为空）不在内，沿用 spec 原值。
    """

    remote_ids = {
        row.peering.remote_node_id
        for row in wg_rows
        if row.peering is not None and row.peering.remote_node_id is not None
    }
    if not remote_ids:
        return {}
    rows = await session.execute(
        select(Node.node_id, Node.wireguard_public_key).where(Node.node_id.in_(remote_ids))
    )
    return {node_id: pubkey for node_id, pubkey in rows if pubkey}


def _zone_spec(zone: DnsGroupZone, records: list[dict]) -> dict:
    """把一个权威 zone + 它的记录组装成 ``DnsZoneSpec`` dict（SOA 留空即自动生成）。"""

    spec: dict = {
        "zone": zone.zone,
        "records_ref": f"zone://{zone.zone}",
        "primary_ns": zone.primary_ns or f"ns.{zone.zone}.",
        "admin_email": zone.admin_email or f"hostmaster.{zone.zone}.",
        "records": records,
    }
    for key, value in (
        ("soa_refresh", zone.soa_refresh),
        ("soa_retry", zone.soa_retry),
        ("soa_expire", zone.soa_expire),
        ("soa_minimum", zone.soa_minimum),
        ("default_ttl", zone.default_ttl),
    ):
        if value is not None:
            spec[key] = value
    return spec


async def _load_dns_group(session: AsyncSession, node: Node) -> dict | None:
    """把节点订阅的共享 DNS 组组装成 ``DnsSpec`` dict（None ⇒ 不部署 DNS）。

    节点未分配组 / 组不存在 / 组被禁用 ⇒ None。否则按 组→权威 zone→记录 组装：每个 enabled
    zone 收齐 enabled 记录、生成内联 ``DnsZoneSpec``（SOA 留空即自动）。无任何可服务 zone 且无
    forwards ⇒ None（没东西服务就不部署）。多节点订阅同一组拿到的就是这同一份配置——anycast。
    ``dns`` 非空触发 DesiredState 校验注入 CoreDNS 服务。
    """

    if node.dns_group_id is None:
        return None
    group = await session.get(DnsGroup, node.dns_group_id)
    if group is None or not group.enabled:
        return None

    zone_rows = await session.execute(
        select(DnsGroupZone)
        .where(DnsGroupZone.dns_group_id == group.id, DnsGroupZone.enabled.is_(True))
        .order_by(DnsGroupZone.zone, DnsGroupZone.id)
    )
    zones: list[dict] = []
    for zone in zone_rows.scalars():
        rec_rows = await session.execute(
            select(DnsRecord)
            .where(DnsRecord.dns_group_zone_id == zone.id, DnsRecord.enabled.is_(True))
            .order_by(DnsRecord.sort_order, DnsRecord.id)
        )
        records = [
            {
                "name": r.name,
                "type": r.type,
                "value": r.content,
                **({"ttl": r.ttl} if r.ttl is not None else {}),
            }
            for r in rec_rows.scalars()
        ]
        if records:  # 空 zone 不输出（Corefile 引用却无 zone 文件会让 CoreDNS 加载失败）。
            zones.append(_zone_spec(zone, records))

    forwards = list(group.forwards or [])
    if not zones and not forwards:
        return None
    return {
        "enabled": True,
        "bind_addresses": list(group.bind_addresses or []),
        "cache_ttl_seconds": group.cache_ttl_seconds,
        "zones": zones,
        "forwards": forwards,
    }


def _interface_payload(
    row: WgInterface, peer_public_keys: dict[str, str], link_local: str | None
) -> dict[str, object]:
    """接口 spec → snapshot dict，对 WG 接口派生注入两类单一真相源：

    - 节点级 LLA（``NodeSpec.link_local``）→ **外部 eBGP** WG 接口 addresses（``fe80::X/64``）。
      渲染器 ``_wireguard_address_commands`` 再与各接口 fe80 ``peer_route`` 配成 peer 形式。
      内部互联（iBGP/OSPF）WG 接口用各自 LL，故要求 ``peering`` 存在且 ``is_internal=False``。
    - 内部对端 WG 公钥（对端 ``Node.wireguard_public_key``）→ ``wireguard_peer.public_key``。

    两者皆 dedup / 现取现填。
    """

    spec = dict(row.spec)
    peering = row.peering

    # 节点级 LLA → 外部 eBGP WG 接口 addresses（单源 NodeSpec.link_local）。
    is_external_wg = (
        spec.get("kind") == InterfaceKind.WIREGUARD.value
        and peering is not None
        and not peering.is_internal
    )
    if link_local and is_external_wg:
        lla = f"{link_local}/64"
        addresses = list(spec.get("addresses") or [])
        if lla not in addresses:
            spec["addresses"] = [*addresses, lla]

    # 内部对端 WG 公钥现取现填（对端 Node.wireguard_public_key）。
    if peering is not None and peering.remote_node_id is not None:
        pubkey = peer_public_keys.get(peering.remote_node_id)
        if pubkey:
            peer = spec.get("wireguard_peer")
            if isinstance(peer, dict):
                spec["wireguard_peer"] = {**peer, "public_key": pubkey}
    return spec


def _assemble_snapshot(
    *,
    node: Node,
    wg_rows: list[WgInterface],
    bgp_rows: list[BgpSession],
    dns_spec: dict | None,
    peer_public_keys: dict[str, str],
    generation: int,
) -> dict[str, object]:
    """把 base_template + 子表内容合并成一个完整 DesiredState dict。"""

    snapshot: dict[str, object] = dict(node.base_template or {})
    snapshot["schema_version"] = snapshot.get("schema_version", "v1")
    snapshot["generation"] = generation
    snapshot["node"] = _node_payload(node, snapshot.get("node"))
    node_link_local = snapshot["node"].get("link_local") if isinstance(snapshot["node"], dict) else None

    # 退役态:产出一份"无对端"的 DesiredState——清空 interfaces / bgp / dns。
    # agent 收敛即拆除所有隧道、撤掉所有 BGP 会话,节点停止宣告任何路由。核心 runtime
    # 服务保留(schema 强制 router-netns/wg-gateway/bird-router 必须 enabled),它们空转。
    decommissioned = getattr(node, "lifecycle", "active") == "decommissioned"

    if decommissioned:
        snapshot["interfaces"] = []
        snapshot["bgp_sessions"] = []
        snapshot["dns"] = None
    else:
        # InterfaceSpec 没有 enabled 字段：disabled 的接口直接不进入 snapshot。
        snapshot["interfaces"] = [
            _interface_payload(row, peer_public_keys, node_link_local)
            for row in wg_rows
            if row.enabled
        ]
        # BgpSessionSpec 有 enabled：disabled 会话仍出现在 snapshot，但 enabled=False。
        snapshot["bgp_sessions"] = [_bgp_payload(row) for row in bgp_rows]
        # DNS 整段来自订阅的共享 DNS 组（不再用 base_template.dns / 节点级 zone）；
        # None ⇒ 不部署 DNS。dns 非空时 DesiredState 校验会注入 CoreDNS 服务。
        snapshot["dns"] = dns_spec

    snapshot.setdefault("templates", {})
    snapshot.setdefault("runtime", snapshot.get("runtime") or {})
    return snapshot


def _node_payload(node: Node, fallback: object) -> dict[str, object]:
    """以 DB 字段优先，base_template.node 作为兜底（提供 region 等不在 DB 中的字段）。"""

    base = dict(fallback) if isinstance(fallback, dict) else {}
    base.update(
        {
            "node_id": node.node_id,
            "site": node.site,
            "asn": node.asn,
            "router_id": node.router_id,
            "ipv4_prefixes": list(node.ipv4_prefixes or []),
            "ipv6_prefixes": list(node.ipv6_prefixes or []),
            "loopback_ipv4": node.loopback_ipv4,
            "loopback_ipv6": node.loopback_ipv6,
            # DB 列为外部 eBGP LLA 单一真相源（与 loopback 同样 DB 字段优先，None 也覆盖
            # base_template，避免两处各存一份）。设了才派生 fe80::X/64，否则 no-op。
            "link_local": node.link_local,
        }
    )
    return base


def _bgp_payload(row: BgpSession) -> dict[str, object]:
    spec = dict(row.spec)
    spec["enabled"] = row.enabled
    return spec


__all__ = ["materialize"]
