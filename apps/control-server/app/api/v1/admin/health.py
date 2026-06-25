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
    FleetOverview,
    NodeHealthDetail,
    NodeStatusEvents,
)
from ....services.desired_state import DesiredStateStore
from ....services.node_status import NodeStatusStore
from ...deps import get_desired_state, get_node_status

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
    kind: Literal["snapshot", "report", "apply", "reresolve"] | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    node_status: NodeStatusStore = Depends(get_node_status),
) -> dict:
    events = await node_status.list_events(node_id, kind=kind, limit=limit)
    return {"node_id": node_id, "events": events}


__all__ = ["router"]
