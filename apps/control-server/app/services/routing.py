from __future__ import annotations

"""节点路由全表仓库 ``RoutingStore``。

把 agent 周期上报的 ``RoutingTableSnapshot`` 持久化到 ``node_routing``（每节点
最新全表 + 预聚合）与 ``node_routing_events``（时间序列 + churn），并对外提供
Radar 式查询：摘要、起源 AS Top 榜、前缀检索、时间线。

非 ``OBSERVED`` 的上报（采集失败 / 未观测）只更新观测状态，**不清空**已有全表
——一次 BIRD 不可达不该让控制面丢失上一份好数据。
"""

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import String, cast, delete, func, or_, select

from dn42_schemas import ObservationStatus, RoutingTableSnapshot

from ..db.engine import Database
from ..db.models import NodeRouteEntry, NodeRouting, NodeRoutingEvent
from .routing_aggregate import aggregate_routes, best_prefix_set, diff_prefix_sets

# 每个节点在时间序列表里最多保留多少条。
_HISTORY_KEEP = 500


def _routes_hash(routes: list[dict]) -> str:
    """全表内容的规范哈希，用来门控明细表是否需要重写（内容未变则跳过）。"""

    blob = json.dumps(routes, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _route_to_entry(node_id: str, route: dict) -> NodeRouteEntry:
    prefix = route.get("prefix") or ""
    origin = route.get("origin_asn")
    return NodeRouteEntry(
        node_id=node_id,
        prefix=prefix,
        is_v6=":" in prefix,
        local=bool(route.get("local")),
        primary=bool(route.get("primary")),
        origin_asn=origin if isinstance(origin, int) else None,
        protocol=route.get("protocol"),
        rpki=route.get("rpki"),
        next_hop=route.get("next_hop"),
        as_path=route.get("as_path") or [],
        communities=route.get("communities") or [],
        large_communities=route.get("large_communities") or [],
    )


def _entry_to_dict(entry: NodeRouteEntry) -> dict:
    """还原成 ``ObservedRoute`` 的 JSON 形态（与早期存 blob 时的查询返回一致）。"""

    return {
        "prefix": entry.prefix,
        "origin_asn": entry.origin_asn,
        "as_path": entry.as_path or [],
        "next_hop": entry.next_hop,
        "protocol": entry.protocol,
        "primary": entry.primary,
        "local": entry.local,
        "communities": entry.communities or [],
        "large_communities": entry.large_communities or [],
        "rpki": entry.rpki,
    }


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class RoutingStore:
    """读写节点路由全表。所有方法各自开一个 session。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def _get_or_create(self, session, node_id: str) -> NodeRouting:
        row = await session.get(NodeRouting, node_id)
        if row is None:
            row = NodeRouting(node_id=node_id)
            session.add(row)
        return row

    async def _trim_history(self, session, node_id: str) -> None:
        keep_ids = (
            select(NodeRoutingEvent.id)
            .where(NodeRoutingEvent.node_id == node_id)
            .order_by(NodeRoutingEvent.id.desc())
            .limit(_HISTORY_KEEP)
        )
        await session.execute(
            delete(NodeRoutingEvent).where(
                NodeRoutingEvent.node_id == node_id,
                NodeRoutingEvent.id.notin_(keep_ids),
            )
        )

    async def _existing_prefixes(self, session, node_id: str) -> set[str]:
        """该节点明细表里现有的去重前缀集合（= 上一份快照），用于算 churn。

        与 ``best_prefix_set`` 同口径：churn 看的是「前缀是否存在」的增减，故取
        ``DISTINCT prefix`` 即可，无需把整行读回。
        """

        rows = await session.execute(
            select(NodeRouteEntry.prefix).where(NodeRouteEntry.node_id == node_id).distinct()
        )
        return {prefix for (prefix,) in rows.all()}

    async def record_snapshot(self, snapshot: RoutingTableSnapshot) -> None:
        captured = _parse_ts(snapshot.captured_at)
        async with self._db.session() as session:
            row = await self._get_or_create(session, snapshot.node_id)
            row.observation = snapshot.observation.value
            if captured is not None:
                row.captured_at = captured

            if snapshot.observation != ObservationStatus.OBSERVED:
                # 采集失败 / 未观测：保留既有全表，仅记录状态与时刻。
                return

            routes = [route.model_dump(mode="json") for route in snapshot.routes]
            # churn：与明细表里的上一份前缀集合比对（不再解析旧 JSON blob）。
            previous = await self._existing_prefixes(session, snapshot.node_id)
            current = best_prefix_set(routes)
            announced, withdrawn = diff_prefix_sets(previous, current)

            aggregates = aggregate_routes(routes)
            # 过滤前(import-table)RPKI 分布:agent 已聚合好,直接存进 aggregates JSON
            # （无需 DB 迁移）。旧 agent 不带 prefilter 时为 None。
            aggregates["prefilter"] = (
                snapshot.prefilter.model_dump(mode="json") if snapshot.prefilter else None
            )
            old_hash = (row.aggregates or {}).get("routes_hash")
            new_hash = _routes_hash(routes)
            aggregates["routes_hash"] = new_hash

            row.aggregates = aggregates
            row.route_count = aggregates["route_count"]
            row.route_count_v4 = aggregates["route_count_v4"]
            row.route_count_v6 = aggregates["route_count_v6"]
            rpki = aggregates["rpki"]
            row.rpki_valid = rpki["valid"]
            row.rpki_invalid = rpki["invalid"]
            row.rpki_not_found = rpki["not_found"]

            # 明细表只在全表内容变化时整体重写，稳定期跳过（省掉上万行反复删插）。
            if new_hash != old_hash:
                await session.execute(
                    delete(NodeRouteEntry).where(NodeRouteEntry.node_id == snapshot.node_id)
                )
                session.add_all(_route_to_entry(snapshot.node_id, route) for route in routes)

            session.add(
                NodeRoutingEvent(
                    node_id=snapshot.node_id,
                    captured_at=captured,
                    route_count=row.route_count,
                    route_count_v4=row.route_count_v4,
                    route_count_v6=row.route_count_v6,
                    rpki_valid=row.rpki_valid,
                    rpki_invalid=row.rpki_invalid,
                    rpki_not_found=row.rpki_not_found,
                    announced=announced,
                    withdrawn=withdrawn,
                )
            )
            await self._trim_history(session, snapshot.node_id)

    async def get_fleet(self) -> dict:
        """跨节点的路由总览：合计规模 + RPKI + 逐节点路由数。

        呼应 ``/admin/health`` 的 fleet 口径——把每个节点最新全表的计数相加，供
        总览面板一眼看清整个机群的路由体量与 RPKI 分布。
        """

        async with self._db.session() as session:
            rows = await session.execute(select(NodeRouting).order_by(NodeRouting.node_id))
            nodes: list[dict] = []
            totals = {
                "route_count": 0,
                "route_count_v4": 0,
                "route_count_v6": 0,
            }
            rpki_total = {"valid": 0, "invalid": 0, "not_found": 0}
            reporting = 0
            for row in rows.scalars():
                reporting += 1
                totals["route_count"] += row.route_count
                totals["route_count_v4"] += row.route_count_v4
                totals["route_count_v6"] += row.route_count_v6
                rpki_total["valid"] += row.rpki_valid
                rpki_total["invalid"] += row.rpki_invalid
                rpki_total["not_found"] += row.rpki_not_found
                nodes.append(
                    {
                        "node_id": row.node_id,
                        "observation": row.observation,
                        "captured_at": _iso(row.captured_at),
                        "route_count": row.route_count,
                        "route_count_v4": row.route_count_v4,
                        "route_count_v6": row.route_count_v6,
                        "rpki": {
                            "valid": row.rpki_valid,
                            "invalid": row.rpki_invalid,
                            "not_found": row.rpki_not_found,
                        },
                    }
                )
            return {
                "summary": {**totals, "rpki": rpki_total, "nodes_reporting": reporting},
                "nodes": nodes,
            }

    async def get_summary(self, node_id: str) -> dict | None:
        async with self._db.session() as session:
            row = await session.get(NodeRouting, node_id)
            if row is None:
                return None
            aggregates = row.aggregates or {}
            return {
                "node_id": row.node_id,
                "observation": row.observation,
                "captured_at": _iso(row.captured_at),
                "updated_at": _iso(row.updated_at),
                "route_count": row.route_count,
                "route_count_v4": row.route_count_v4,
                "route_count_v6": row.route_count_v6,
                "local_count": aggregates.get("local_count", 0),
                "rpki": {
                    "valid": row.rpki_valid,
                    "invalid": row.rpki_invalid,
                    "not_found": row.rpki_not_found,
                },
                # ROA 表整张采集失败时 False,前端显式提示(不再悄悄塞进「未知」)。
                "rpki_observed": aggregates.get("rpki_observed", True),
                "prefix_lengths": aggregates.get("prefix_lengths", {"4": {}, "6": {}}),
                "as_path_lengths": aggregates.get("as_path_lengths", {}),
                "peers": aggregates.get("peers", []),
                "prefilter": aggregates.get("prefilter"),
            }

    async def get_origins(self, node_id: str, *, limit: int = 50) -> dict | None:
        async with self._db.session() as session:
            row = await session.get(NodeRouting, node_id)
            if row is None:
                return None
            origins = (row.aggregates or {}).get("origins", [])
            return {
                "node_id": node_id,
                "total": len(origins),
                "origins": origins[:limit],
            }

    async def get_prefixes(
        self,
        node_id: str,
        *,
        family: str | None = None,
        local: bool | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict | None:
        async with self._db.session() as session:
            # 节点从未上报 ⇒ None（404 语义）；上报过但无匹配 ⇒ total=0 + 空列表。
            if await session.get(NodeRouting, node_id) is None:
                return None

            conditions = [NodeRouteEntry.node_id == node_id]
            if family in {"4", "6"}:
                conditions.append(NodeRouteEntry.is_v6.is_(family == "6"))
            if local is not None:
                conditions.append(NodeRouteEntry.local.is_(local))
            if query:
                needle = query.strip().lower()
                like = f"%{needle}%"
                conditions.append(
                    or_(
                        func.lower(NodeRouteEntry.prefix).like(like),
                        func.lower(NodeRouteEntry.protocol).like(like),
                        cast(NodeRouteEntry.origin_asn, String).like(f"%{needle}%"),
                    )
                )

            total = await session.scalar(
                select(func.count()).select_from(NodeRouteEntry).where(*conditions)
            )
            rows = await session.execute(
                select(NodeRouteEntry)
                .where(*conditions)
                .order_by(NodeRouteEntry.id)
                .limit(limit)
                .offset(offset)
            )
            return {
                "node_id": node_id,
                "total": total or 0,
                "limit": limit,
                "offset": offset,
                "routes": [_entry_to_dict(entry) for entry in rows.scalars()],
            }

    async def get_timeline(self, node_id: str, *, limit: int = 200) -> dict:
        async with self._db.session() as session:
            stmt = (
                select(NodeRoutingEvent)
                .where(NodeRoutingEvent.node_id == node_id)
                .order_by(NodeRoutingEvent.id.desc())
                .limit(limit)
            )
            rows = await session.execute(stmt)
            events = [
                {
                    "id": ev.id,
                    "captured_at": _iso(ev.captured_at),
                    "created_at": _iso(ev.created_at),
                    "route_count": ev.route_count,
                    "route_count_v4": ev.route_count_v4,
                    "route_count_v6": ev.route_count_v6,
                    "rpki": {
                        "valid": ev.rpki_valid,
                        "invalid": ev.rpki_invalid,
                        "not_found": ev.rpki_not_found,
                    },
                    "announced": ev.announced,
                    "withdrawn": ev.withdrawn,
                }
                for ev in rows.scalars()
            ]
            # 时间线按时间升序更适合画图；DB 取的是最近 N 条（倒序），这里翻正。
            events.reverse()
            return {"node_id": node_id, "events": events}


__all__ = ["RoutingStore"]
