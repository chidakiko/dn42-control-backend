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
from datetime import datetime, timedelta, timezone

from sqlalchemy import String, cast, delete, func, or_, select

from dn42_schemas import ObservationStatus, RoutingTableSnapshot

from ..db.engine import Database
from ..db.models import NodeRouteEntry, NodeRouting, NodeRoutingEvent
from .cache import Cache
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

    # 路由聚合 30s 才变一次（agent 300s 上报），Web 轮询期间命中缓存，写入主动失效。
    _ROUTING_TTL = 30

    def __init__(self, database: Database, *, cache: "Cache | None" = None) -> None:
        self._db = database
        self._cache = cache or Cache(None)

    async def _bust_routing(self, node_id: str) -> None:
        """节点路由上报后失效 fleet 总览 + 该节点 summary（事务 commit 之后调用）。"""

        await self._cache.delete("routing:fleet", f"routing:summary:{node_id}")

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

            # 内容未变（哈希一致）⇒ 前缀集合必然恒等 ⇒ churn=0：跳过 50k 行 DISTINCT +
            # best_prefix_set + 明细整表重写。只在变化时才付这份代价。
            if new_hash != old_hash:
                previous = await self._existing_prefixes(session, snapshot.node_id)
                announced, withdrawn = diff_prefix_sets(previous, best_prefix_set(routes))
                await session.execute(
                    delete(NodeRouteEntry).where(NodeRouteEntry.node_id == snapshot.node_id)
                )
                session.add_all(_route_to_entry(snapshot.node_id, route) for route in routes)
            else:
                announced, withdrawn = 0, 0

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
        await self._bust_routing(snapshot.node_id)  # commit 后失效 fleet/summary 缓存

    async def get_fleet(self) -> dict:
        """跨节点的路由总览：合计规模 + RPKI + 逐节点路由数。

        呼应 ``/admin/health`` 的 fleet 口径——把每个节点最新全表的计数相加，供
        总览面板一眼看清整个机群的路由体量与 RPKI 分布。
        """

        cached = await self._cache.get_json("routing:fleet")
        if cached is not None:
            return cached
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
            result = {
                "summary": {**totals, "rpki": rpki_total, "nodes_reporting": reporting},
                "nodes": nodes,
            }
        await self._cache.set_json("routing:fleet", result, ttl_seconds=self._ROUTING_TTL)
        return result

    async def get_fleet_overview(
        self, *, window_hours: int = 8, bucket_seconds: int = 300, origins_limit: int = 12
    ) -> dict:
        """WebUI 专用聚合:fleet 路由总览(``get_fleet`` 的 summary+nodes)+ **服务端算好的
        路由表规模/churn 时间线** + **全机群 Top 起源 AS 榜**,供概览页「路由全表」一次取全。

        iBGP 收敛后各节点 RIB 趋同,故 ``trend`` 把全节点 ``node_routing_events`` 按
        ``bucket_seconds`` 对齐:每桶取各节点 ``route_count`` 中位数(代表全机群表规模),
        并对 ``announced`` / ``withdrawn`` 求和(路由变化 churn)。取代前端逐节点拉 timeline。

        ``origins`` 同理:各节点起源 AS 计数趋同,per-ASN 取各节点最大计数代表全机群
        (落后节点计数偏小,max 取健康值),按计数降序取前 ``origins_limit``。
        """

        fleet = await self.get_fleet()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
        sizes: dict[int, list[int]] = {}
        ann: dict[int, int] = {}
        wd: dict[int, int] = {}
        origin_max: dict[int, int] = {}
        async with self._db.session() as session:
            rows = await session.execute(
                select(
                    NodeRoutingEvent.created_at,
                    NodeRoutingEvent.route_count,
                    NodeRoutingEvent.announced,
                    NodeRoutingEvent.withdrawn,
                ).where(NodeRoutingEvent.created_at >= cutoff)
            )
            for created, count, a, w in rows.all():
                if created is None:
                    continue
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                b = int(created.timestamp()) // bucket_seconds * bucket_seconds
                sizes.setdefault(b, []).append(count or 0)
                ann[b] = ann.get(b, 0) + (a or 0)
                wd[b] = wd.get(b, 0) + (w or 0)

            agg_rows = await session.execute(select(NodeRouting.aggregates))
            for (aggregates,) in agg_rows.all():
                for origin in (aggregates or {}).get("origins", []):
                    asn = origin.get("asn")
                    if asn is None:
                        continue
                    count = origin.get("count", 0) or 0
                    if count > origin_max.get(asn, 0):
                        origin_max[asn] = count
        trend: list[dict] = []
        for b in sorted(sizes):
            vals = sorted(sizes[b])
            m = len(vals) // 2
            med = vals[m] if len(vals) % 2 else (vals[m - 1] + vals[m]) // 2
            trend.append(
                {
                    "captured_at": datetime.fromtimestamp(b, tz=timezone.utc).isoformat(),
                    "size": med,
                    "announced": ann.get(b, 0),
                    "withdrawn": wd.get(b, 0),
                }
            )
        origins = [
            {"asn": asn, "count": count}
            for asn, count in sorted(origin_max.items(), key=lambda kv: (-kv[1], kv[0]))
        ][:origins_limit]
        return {**fleet, "trend": trend, "origins": origins}

    @staticmethod
    def _summary_dict(row: NodeRouting) -> dict:
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

    @staticmethod
    def _origins_dict(row: NodeRouting, *, limit: int) -> dict:
        origins = (row.aggregates or {}).get("origins", [])
        return {
            "node_id": row.node_id,
            "total": len(origins),
            "origins": origins[:limit],
        }

    async def _timeline_dict(self, session, node_id: str, *, limit: int) -> dict:
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

    async def get_summary(self, node_id: str) -> dict | None:
        cache_key = f"routing:summary:{node_id}"
        cached = await self._cache.get_json(cache_key)
        if cached is not None:
            return cached
        async with self._db.session() as session:
            row = await session.get(NodeRouting, node_id)
            if row is None:
                return None
            result = self._summary_dict(row)
        await self._cache.set_json(cache_key, result, ttl_seconds=self._ROUTING_TTL)
        return result

    async def get_origins(self, node_id: str, *, limit: int = 50) -> dict | None:
        async with self._db.session() as session:
            row = await session.get(NodeRouting, node_id)
            if row is None:
                return None
            return self._origins_dict(row, limit=limit)

    async def get_dashboard(
        self, node_id: str, *, origins_limit: int = 15, timeline_limit: int = 200
    ) -> dict | None:
        """RoutingTab「头部」三件套一次取全：summary + origins + timeline。

        前端进页 / 每个刷新 tick 原本各拉 3 个端点（3 次跨网往返）；这里一个 session
        读一次 ``NodeRouting`` 行 + 一次事件查询即拼齐，省两次往返（跨 GFW 时延敏感）。
        节点从未上报 ⇒ ``None``（404 语义）。
        """

        async with self._db.session() as session:
            row = await session.get(NodeRouting, node_id)
            if row is None:
                return None
            return {
                "node_id": node_id,
                "summary": self._summary_dict(row),
                "origins": self._origins_dict(row, limit=origins_limit),
                "timeline": await self._timeline_dict(session, node_id, limit=timeline_limit),
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
            return await self._timeline_dict(session, node_id, limit=limit)


__all__ = ["RoutingStore"]
