from __future__ import annotations

"""WebUI 路由相关聚合端点(只读 BFF)。

- ``GET /ui/nodes/{node_id}/internal-topology``：iBGP + OSPF 内部互联视图(配置 + 路由层 liveness)。
- ``GET /ui/routing/fleet-overview``：概览页「路由全表」板块一次取全(summary + 每节点 + 规模/churn 趋势)。
- ``GET /ui/nodes/{node_id}/routing/dashboard``：RoutingTab 头部一次取全(summary + origins + timeline)。

通用的细粒度路由检索(``/routing/{fleet,summary,origins,prefixes,timeline}``)留在
``/admin`` 下,供对接其他系统用。
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....schemas.routing import (
    FleetRoutingOverview,
    InternalTopologyView,
    RoutingDashboard,
)
from ....services.desired_state import DesiredStateStore
from ....services.internal_topology import build_internal_topology_view
from ....services.routing import RoutingStore
from ...deps import get_desired_state, get_routing

router = APIRouter()


@router.get("/nodes/{node_id}/internal-topology", response_model=InternalTopologyView)
async def node_internal_topology(
    node_id: str,
    desired: DesiredStateStore = Depends(get_desired_state),
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    """iBGP + OSPF 内部互联视图：``bird.internal_topology`` 配置 + 路由层 liveness。

    iBGP/OSPF 不是 ``bgp_sessions`` 记录（看不到于"BGP 会话"面板），由 internal
    topology 自动合成；这里把它单独暴露，并关联路由快照里各协议贡献的最优路由数。
    """

    state = await desired.get(node_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"node {node_id} has no published desired state",
        )
    summary = await routing.get_summary(node_id)
    return build_internal_topology_view(node_id, state.bird.internal_topology, summary)


@router.get("/routing/fleet-overview", response_model=FleetRoutingOverview)
async def routing_fleet_overview(
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    """概览页「路由全表」板块一次取全:summary + 每节点 + 服务端聚合的规模/churn 趋势。"""

    return await routing.get_fleet_overview()


@router.get("/nodes/{node_id}/routing/dashboard", response_model=RoutingDashboard)
async def routing_dashboard(
    node_id: str,
    origins_limit: int = Query(default=15, ge=1, le=1000),
    timeline_limit: int = Query(default=200, ge=1, le=500),
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    """RoutingTab 头部一次拉全：summary + origins + timeline（取代 3 次跨网往返）。"""

    data = await routing.get_dashboard(
        node_id, origins_limit=origins_limit, timeline_limit=timeline_limit
    )
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no routing table reported for node {node_id}",
        )
    return data


__all__ = ["router"]
