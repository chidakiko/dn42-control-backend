from __future__ import annotations

"""路由全表的纯聚合函数（Radar 式分析的计算核心）。

输入是 agent 上报的原始路由 dict 列表（``ObservedRoute`` 的 JSON 形态），输出
是可直接喂给前端图表的聚合结构。刻意做成无副作用的纯函数，便于单测覆盖各种
分布与边界，与 DB / ORM 解耦。

口径：以**最优路径**（同前缀去重，``primary`` 优先）为准，等价于该节点 RIB 的
最佳路由视图——与 Cloudflare Radar 的"路由数"口径一致。
"""

from collections import Counter


def family_of(prefix: str) -> str:
    """从 CIDR 文本判定地址族：``"6"`` 含冒号，否则 ``"4"``。"""

    return "6" if ":" in prefix else "4"


def _best_by_prefix(routes: list[dict]) -> list[dict]:
    """同前缀去重，保留最优路径（``primary`` 优先，否则首次出现）。"""

    best: dict[str, dict] = {}
    for route in routes:
        prefix = route.get("prefix")
        if not isinstance(prefix, str):
            continue
        if prefix not in best or route.get("primary"):
            best[prefix] = route
    return list(best.values())


def _rpki_bucket(value: object) -> str | None:
    """把 RPKI 结论归一到三个真实状态(valid/invalid/not_found)。

    无法判定的(``None`` / 其它，多半是 ROA 表整张采集失败)返回 ``None``，
    **不计入**任何桶——改由 ``rpki_observed`` 标记 ROA 是否采到，前端显式提示。
    """

    if value in {"valid", "invalid", "not-found"}:
        return str(value).replace("-", "_")
    return None


def aggregate_routes(routes: list[dict]) -> dict:
    """把原始路由列表聚合成图表友好的统计结构。"""

    best = _best_by_prefix(routes)

    route_count_v4 = 0
    route_count_v6 = 0
    local_count = 0
    non_local = 0
    rpki = {"valid": 0, "invalid": 0, "not_found": 0}
    origins: Counter[int] = Counter()
    prefix_lengths_v4: Counter[int] = Counter()
    prefix_lengths_v6: Counter[int] = Counter()
    as_path_lengths: Counter[int] = Counter()
    peers: Counter[str] = Counter()

    for route in best:
        prefix = route["prefix"]
        family = family_of(prefix)
        try:
            length = int(prefix.split("/", 1)[1])
        except (IndexError, ValueError):
            length = 0
        if family == "6":
            route_count_v6 += 1
            prefix_lengths_v6[length] += 1
        else:
            route_count_v4 += 1
            prefix_lengths_v4[length] += 1

        if route.get("local"):
            local_count += 1
        else:
            # 本地路由不参与 RPKI 分布（它们不对外宣告，不做起源校验）。
            non_local += 1
            bucket = _rpki_bucket(route.get("rpki"))
            if bucket is not None:
                rpki[bucket] += 1

        origin = route.get("origin_asn")
        if isinstance(origin, int):
            origins[origin] += 1

        as_path = route.get("as_path")
        as_path_lengths[len(as_path) if isinstance(as_path, list) else 0] += 1

        protocol = route.get("protocol")
        if isinstance(protocol, str) and protocol:
            peers[protocol] += 1

    # ROA 是否采到:有外部路由却一条都没分类出来(全 None) ⇒ 整张 ROA 表没采到。
    rpki_observed = not (non_local > 0 and (rpki["valid"] + rpki["invalid"] + rpki["not_found"]) == 0)

    return {
        "route_count": route_count_v4 + route_count_v6,
        "route_count_v4": route_count_v4,
        "route_count_v6": route_count_v6,
        "local_count": local_count,
        "rpki": rpki,
        "rpki_observed": rpki_observed,
        # origins / peers：完整列表按计数降序，查询接口再按需截断 Top-N。
        "origins": [
            {"asn": asn, "count": count}
            for asn, count in origins.most_common()
        ],
        "peers": [
            {"protocol": protocol, "count": count}
            for protocol, count in peers.most_common()
        ],
        "prefix_lengths": {
            "4": {str(k): v for k, v in sorted(prefix_lengths_v4.items())},
            "6": {str(k): v for k, v in sorted(prefix_lengths_v6.items())},
        },
        "as_path_lengths": {str(k): v for k, v in sorted(as_path_lengths.items())},
    }


def best_prefix_set(routes: list[dict]) -> set[str]:
    """最优路径的前缀集合，用于跨快照算 churn。"""

    return {route["prefix"] for route in _best_by_prefix(routes)}


def diff_prefix_sets(old: set[str], new: set[str]) -> tuple[int, int]:
    """返回 (新增前缀数, 撤销前缀数)。"""

    return len(new - old), len(old - new)


__all__ = [
    "aggregate_routes",
    "best_prefix_set",
    "diff_prefix_sets",
    "family_of",
]
