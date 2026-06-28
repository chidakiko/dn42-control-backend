from __future__ import annotations

"""WebUI 观测聚合端点(只读 BFF)。

把「读时扒 ``last_snapshot`` / 浏览器算差分 / 逐节点 N 次拉取」挪到服务端:

- ``GET /ui/fleet/overview``：fleet 健康 + 每节点能力 + 物理 WG 邻接网格,总览页单次拉取。
- ``GET /ui/nodes/{node_id}/traffic``、``GET /ui/fleet/traffic``：WG 吞吐时间线(字节/秒)。
- ``GET /ui/nodes/{node_id}/links``：链路状态(服务端判 up/stale/down)。
- ``GET /ui/nodes/{node_id}/bgp-sessions/status``：内 iBGP + 外 eBGP 综合状态(按配置归类)。
- ``GET /ui/nodes/{node_id}/overview``：节点页一次取全(健康行 + 能力 + 自观测 + drift + 链路/BGP)。

纯只读聚合,不动 agent 协议。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....schemas.health import (
    FleetOverview,
    FleetTraffic,
    NodeBgpSessions,
    NodeHealthRow,
    NodeLinks,
    NodeOverview,
    NodeTraffic,
)
from ....services.desired_state import DesiredStateStore
from ....services.node_status import NodeStatusStore
from ....services.observability import (
    aggregate_fleet_traffic,
    compute_node_traffic,
    node_bgp_sessions,
    node_links,
)
from ....services.traffic import TrafficStore
from ...deps import get_desired_state, get_node_status, get_traffic

router = APIRouter()


@router.get("/fleet/overview", response_model=FleetOverview)
async def fleet_overview(
    node_status: NodeStatusStore = Depends(get_node_status),
    desired: DesiredStateStore = Depends(get_desired_state),
) -> dict:
    """一次性聚合 fleet 健康 + 每节点服务能力 + 全网物理 WG 邻接网格(只读)。

    供 Web 仪表盘单次拉取,取代逐节点 N 次调用:

    - ``summary`` / ``nodes`` 与 ``GET /admin/health`` 同源(``NodeStatusStore.list_all``);
      每个节点行额外挂 ``capabilities``——其 DesiredState ``runtime.services`` 中 ``enabled``
      的 ``role.value`` 去重排序(无 DesiredState 时空列表)。
    - ``links`` 由各节点 ``bird.internal_topology.igp_adjacencies`` 折叠成无向去重边:
      端点按字典序定为 ``a``/``b``,两侧接口名分别填 ``a_iface``/``b_iface``,``cost``
      取任一侧的非空值。
    """

    nodes = await node_status.list_all()
    summary: dict[str, int] = {}
    edges: dict[tuple[str, str], dict] = {}

    for node in nodes:
        node_id = node["node_id"]
        summary[node["health"]] = summary.get(node["health"], 0) + 1

        state = await desired.get(node_id)
        if state is None:
            node["capabilities"] = []
            continue

        node["capabilities"] = sorted(
            {svc.role.value for svc in state.runtime.services if svc.enabled}
        )

        topology = state.bird.internal_topology
        if topology is None:
            continue
        for adjacency in topology.igp_adjacencies:
            peer = adjacency.node
            if peer == node_id:
                continue  # 跳过自环
            a, b = sorted([node_id, peer])
            edge = edges.setdefault(
                (a, b),
                {"a": a, "b": b, "a_iface": None, "b_iface": None, "cost": None},
            )
            if node_id == a:
                edge["a_iface"] = adjacency.interface
            else:
                edge["b_iface"] = adjacency.interface
            if adjacency.cost is not None:
                edge["cost"] = adjacency.cost

    links = [edges[key] for key in sorted(edges)]
    return {"summary": summary, "nodes": nodes, "links": links}


async def _node_traffic_series(
    node_id: str, *, traffic: TrafficStore, node_status: NodeStatusStore, limit: int
) -> list[dict]:
    """单节点吞吐时间线，三层降级：30s 热窗口 / 5min 存档（TrafficStore）→ 快照差分。

    优先用 agent 30s 轻量采样（TrafficStore：Redis 热窗口或 PG 5min 存档）；尚无采样
    （旧 agent / 刚部署 / Redis 与存档皆空）时回落到从快照历史差分（~5min），保证向后兼容。
    """

    series = await traffic.node_series(node_id)
    if series:
        return series
    events = await node_status.list_events(node_id, kind="snapshot", limit=limit)
    return compute_node_traffic(events)


@router.get("/nodes/{node_id}/traffic", response_model=NodeTraffic)
async def node_traffic(
    node_id: str,
    limit: int = Query(default=120, ge=2, le=500),
    node_status: NodeStatusStore = Depends(get_node_status),
    traffic: TrafficStore = Depends(get_traffic),
) -> dict:
    """单节点 WG 吞吐时间线:服务端给出字节/秒,前端不必拉全量快照再算。

    优先 agent 30s 轻量采样(高分辨率),无采样时回落快照差分(~5min)。
    """

    points = await _node_traffic_series(
        node_id, traffic=traffic, node_status=node_status, limit=limit
    )
    return {"node_id": node_id, "points": points}


@router.get("/fleet/traffic", response_model=FleetTraffic)
async def fleet_traffic(
    limit: int = Query(default=120, ge=2, le=500),
    node_status: NodeStatusStore = Depends(get_node_status),
    traffic: TrafficStore = Depends(get_traffic),
) -> dict:
    """全 fleet 吞吐时间线:各节点逐区间速率按时间桶对齐求和,供总览页单次拉取。

    每节点同样三层降级(30s 采样优先,回落快照差分),再跨节点按 5min 桶对齐求和。
    """

    nodes = await node_status.list_all()
    per_node: list[list[dict]] = []
    for node in nodes:
        per_node.append(
            await _node_traffic_series(
                node["node_id"], traffic=traffic, node_status=node_status, limit=limit
            )
        )
    return {"points": aggregate_fleet_traffic(per_node)}


@router.get("/nodes/{node_id}/links", response_model=NodeLinks)
async def node_links_view(
    node_id: str,
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    """单节点链路状态(类型化):服务端按握手新鲜度判 up/stale/down,免前端扒 last_snapshot。"""

    data = await node_status.get(node_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no runtime status reported for node {node_id}",
        )
    return {"node_id": node_id, "links": node_links(data.get("last_snapshot"))}


@router.get("/nodes/{node_id}/bgp-sessions/status", response_model=NodeBgpSessions)
async def node_bgp_sessions_view(
    node_id: str,
    node_status: NodeStatusStore = Depends(get_node_status),
    desired: DesiredStateStore = Depends(get_desired_state),
) -> dict:
    """单节点 BGP 会话综合状态:内 iBGP + 外 eBGP,内外按 DesiredState 配置归类(比前端启发准)。"""

    data = await node_status.get(node_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no runtime status reported for node {node_id}",
        )
    state = await desired.get(node_id)
    configured = {session.name for session in state.bgp_sessions} if state is not None else set()
    return {"node_id": node_id, "sessions": node_bgp_sessions(data.get("last_snapshot"), configured)}


@router.get("/nodes/{node_id}/overview", response_model=NodeOverview)
async def node_overview(
    node_id: str,
    node_status: NodeStatusStore = Depends(get_node_status),
    desired: DesiredStateStore = Depends(get_desired_state),
) -> dict:
    """节点页一次拉全:健康行 + 能力 + 自观测 + 当前 drift + 链路状态 + BGP 会话状态。

    取代节点页 + 多个子组件各自拉 health / 扒 last_snapshot —— 一次请求,服务端把该派生
    的都派生好(链路/BGP 状态、能力),前端只渲染。历史序列仍走 status-events。
    """

    data = await node_status.get(node_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no runtime status reported for node {node_id}",
        )
    snapshot = data.get("last_snapshot")
    report = data.get("last_report") or {}
    state = await desired.get(node_id)
    capabilities = (
        sorted({svc.role.value for svc in state.runtime.services if svc.enabled})
        if state is not None
        else []
    )
    configured = {session.name for session in state.bgp_sessions} if state is not None else set()

    overview = {field: data.get(field) for field in NodeHealthRow.model_fields}
    overview.update(
        {
            "capabilities": capabilities,
            "self_metrics": (snapshot or {}).get("self_metrics"),
            "drift": report.get("drift") or [],
            "links": node_links(snapshot),
            "bgp_sessions": node_bgp_sessions(snapshot, configured),
        }
    )
    return overview


__all__ = ["router"]
