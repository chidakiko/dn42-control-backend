from __future__ import annotations

"""节点路由全表只读视图（管理面 / 通用接口）。

数据来自 ``RoutingStore``（agent 周期上报的 ``RoutingTableSnapshot`` 聚合）。这些是
细粒度的通用读接口（供对接其他系统用）：

- ``GET /admin/routing/fleet``：全 fleet 路由概览。
- ``GET /admin/nodes/{id}/routing/summary``：全表规模 + RPKI / 前缀长度 / AS path 分布。
- ``GET /admin/nodes/{id}/routing/origins``：起源 AS Top 榜。
- ``GET /admin/nodes/{id}/routing/prefixes``：分页 / 过滤的路由检索。
- ``GET /admin/nodes/{id}/routing/timeline``：路由表趋势 + churn。

WebUI 专用的聚合视图（routing/fleet-overview、routing/dashboard、internal-topology）
已挪到 ``/api/v1/ui`` 下（见 ``api/v1/ui/routing.py``）。
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....schemas.routing import (
    FleetRouting,
    RoutingOrigins,
    RoutingPrefixes,
    RoutingSummary,
    RoutingTimeline,
)
from ....services.routing import RoutingStore
from ...deps import get_routing

router = APIRouter()


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
