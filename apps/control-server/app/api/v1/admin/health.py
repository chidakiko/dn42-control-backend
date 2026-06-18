from __future__ import annotations

"""节点运行时健康只读视图（管理面）。

数据来自 ``NodeStatusStore``（agent 上报持久化）：

- ``GET /admin/health``：整个 fleet 的健康概览。
- ``GET /admin/nodes/{node_id}/health``：单节点健康 + 最近一次 snapshot/report/apply。
- ``GET /admin/nodes/{node_id}/status-events``：单节点上报历史（可按 kind 过滤）。
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ....schemas.health import (
    FleetHealth,
    NodeHealthDetail,
    NodeStatusEvents,
)
from ....services.node_status import NodeStatusStore
from ...deps import get_node_status

router = APIRouter()


@router.get("/health", response_model=FleetHealth)
async def fleet_health(
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    nodes = await node_status.list_all()
    summary: dict[str, int] = {}
    for node in nodes:
        summary[node["health"]] = summary.get(node["health"], 0) + 1
    return {"summary": summary, "nodes": nodes}


@router.get("/nodes/{node_id}/health", response_model=NodeHealthDetail)
async def node_health(
    node_id: str,
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    data = await node_status.get(node_id)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no runtime status reported for node {node_id}",
        )
    return data


@router.get("/nodes/{node_id}/status-events", response_model=NodeStatusEvents)
async def node_status_events(
    node_id: str,
    kind: Literal["snapshot", "report", "apply"] | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    events = await node_status.list_events(node_id, kind=kind, limit=limit)
    return {"node_id": node_id, "events": events}


__all__ = ["router"]
