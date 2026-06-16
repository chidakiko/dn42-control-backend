from __future__ import annotations

"""节点路由全表只读视图（管理面）。

数据来自 ``RoutingStore``（agent 周期上报的 ``RoutingTableSnapshot`` 聚合）：

- ``GET /admin/nodes/{id}/routing/summary``：全表规模 + RPKI / 前缀长度 / AS path 分布。
- ``GET /admin/nodes/{id}/routing/origins``：起源 AS Top 榜。
- ``GET /admin/nodes/{id}/routing/prefixes``：分页 / 过滤的路由检索。
- ``GET /admin/nodes/{id}/routing/timeline``：路由表趋势 + churn。
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....schemas.routing import (
    FleetRouting,
    InternalTopologyView,
    RoutingOrigins,
    RoutingPrefixes,
    RoutingSummary,
    RoutingTimeline,
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


@router.get("/routing/fleet", response_model=FleetRouting)
async def routing_fleet(
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    return await routing.get_fleet()


@router.get("/nodes/{node_id}/routing/summary", response_model=RoutingSummary)
async def routing_summary(
    node_id: str,
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    data = await routing.get_summary(node_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no routing table reported for node {node_id}",
        )
    return data


@router.get("/nodes/{node_id}/routing/origins", response_model=RoutingOrigins)
async def routing_origins(
    node_id: str,
    limit: int = Query(default=50, ge=1, le=1000),
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    data = await routing.get_origins(node_id, limit=limit)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no routing table reported for node {node_id}",
        )
    return data


@router.get("/nodes/{node_id}/routing/prefixes", response_model=RoutingPrefixes)
async def routing_prefixes(
    node_id: str,
    family: Literal["4", "6"] | None = None,
    scope: Literal["all", "local", "external"] = "all",
    q: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    local = {"all": None, "local": True, "external": False}[scope]
    data = await routing.get_prefixes(
        node_id, family=family, local=local, query=q, limit=limit, offset=offset
    )
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no routing table reported for node {node_id}",
        )
    return data


@router.get("/nodes/{node_id}/routing/timeline", response_model=RoutingTimeline)
async def routing_timeline(
    node_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    routing: RoutingStore = Depends(get_routing),
) -> dict:
    return await routing.get_timeline(node_id, limit=limit)


__all__ = ["router"]
