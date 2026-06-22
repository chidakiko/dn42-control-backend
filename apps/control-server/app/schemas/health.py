from __future__ import annotations

"""管理面健康 / 状态视图的响应 DTO。

这些是 admin API 的响应契约(强类型,挂 response_model),与 agent↔server 的
核心协议(dn42_schemas)分开:健康是控制面从 agent 上报派生出的视图。
``health`` 用 dn42_schemas 的 ``NodeHealth`` 枚举,避免散落字符串字面量。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict

from dn42_schemas import NodeHealth


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeHealthRow(_Dto):
    """单节点健康概览(fleet 列表与单节点详情共用的头部)。"""

    node_id: str
    health: NodeHealth
    desired_generation: int | None = None
    observed_generation: int | None = None
    last_report_status: str | None = None
    last_apply_status: str | None = None
    drift_count: int = 0
    last_snapshot_at: str | None = None
    last_report_at: str | None = None
    last_apply_at: str | None = None
    updated_at: str | None = None


class FleetHealth(_Dto):
    """``GET /admin/health``:整个 fleet 的健康概览。"""

    summary: dict[NodeHealth, int]
    nodes: list[NodeHealthRow]


class NodeHealthDetail(NodeHealthRow):
    """``GET /admin/nodes/{id}/health``:单节点健康 + 最近三类上报原文。"""

    last_snapshot: dict[str, Any] | None = None
    last_report: dict[str, Any] | None = None
    last_apply: dict[str, Any] | None = None


class StatusEvent(_Dto):
    """``node_status_events`` 的一条历史记录。"""

    id: int
    kind: str
    generation: int | None = None
    status: str | None = None
    created_at: str | None = None
    payload: dict[str, Any]


class NodeStatusEvents(_Dto):
    """``GET /admin/nodes/{id}/status-events``:单节点上报历史。"""

    node_id: str
    events: list[StatusEvent]


__all__ = [
    "FleetHealth",
    "NodeHealthDetail",
    "NodeHealthRow",
    "NodeStatusEvents",
    "StatusEvent",
]
