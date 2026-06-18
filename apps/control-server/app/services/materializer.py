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

from dn42_schemas import DesiredState

from ..db.models import BgpSession, DnsZone, Node, WgInterface, Generation

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
    snapshot = _assemble_snapshot(
        node=node,
        wg_rows=await _load_interfaces(session, node_id),
        bgp_rows=await _load_sessions(session, node_id),
        dns_rows=await _load_dns_zones(session, node_id),
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


async def _load_dns_zones(session: AsyncSession, node_id: str) -> list[DnsZone]:
    rows = await session.execute(
        select(DnsZone)
        .where(DnsZone.node_id == node_id, DnsZone.enabled.is_(True))
        .order_by(DnsZone.name, DnsZone.id)
    )
    return list(rows.scalars())


def _assemble_snapshot(
    *,
    node: Node,
    wg_rows: list[WgInterface],
    bgp_rows: list[BgpSession],
    dns_rows: list[DnsZone],
    generation: int,
) -> dict[str, object]:
    """把 base_template + 子表内容合并成一个完整 DesiredState dict。"""

    snapshot: dict[str, object] = dict(node.base_template or {})
    snapshot["schema_version"] = snapshot.get("schema_version", "v1")
    snapshot["generation"] = generation
    snapshot["node"] = _node_payload(node, snapshot.get("node"))

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
        snapshot["interfaces"] = [dict(row.spec) for row in wg_rows if row.enabled]
        # BgpSessionSpec 有 enabled：disabled 会话仍出现在 snapshot，但 enabled=False。
        snapshot["bgp_sessions"] = [_bgp_payload(row) for row in bgp_rows]
        snapshot["dns"] = _merge_dns(snapshot.get("dns"), dns_rows)

    snapshot.setdefault("templates", {})
    snapshot.setdefault("runtime", snapshot.get("runtime") or {})
    return snapshot


def _merge_dns(base_dns: object, dns_rows: list[DnsZone]) -> object:
    """把 DnsZone 行注入到 ``base_template.dns.zones``。

    - ``base_template.dns`` 为 None / 缺失：直接返回 None（即使有 zones 行也不输出，
      避免 schema 校验失败——DnsSpec 需要 ``bind_addresses`` 等顶层字段）。
    - 否则覆盖 ``zones`` 字段为 DnsZone 行的 spec。
    """

    if not isinstance(base_dns, dict):
        return None
    merged = dict(base_dns)
    merged["zones"] = [dict(row.spec) for row in dns_rows]
    return merged


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
        }
    )
    return base


def _bgp_payload(row: BgpSession) -> dict[str, object]:
    spec = dict(row.spec)
    spec["enabled"] = row.enabled
    return spec


__all__ = ["materialize"]
