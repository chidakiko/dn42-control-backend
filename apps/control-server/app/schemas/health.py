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


class FleetOverviewNode(NodeHealthRow):
    """``GET /admin/fleet/overview`` 里的单节点行:健康概览 + 该节点启用的服务角色。

    ``capabilities`` 取自 DesiredState 的 ``runtime.services`` 中 ``enabled`` 的
    ``role.value``(去重排序),例如 ``["bird-router", "rpki-cache", ...]``;无 DesiredState
    时为空列表。继承 ``NodeHealthRow`` 的全部字段。
    """

    capabilities: list[str] = []


class FleetLink(_Dto):
    """fleet 物理 WireGuard 网格(OSPF/IGP 邻接)的一条无向去重边。

    ``a``/``b`` 是字典序排序后的两端节点;``a_iface``/``b_iface`` 是各自侧承载该邻接的
    接口名(可能为 ``None``);``cost`` 取任一侧提供的非空开销。
    """

    a: str
    b: str
    a_iface: str | None = None
    b_iface: str | None = None
    cost: int | None = None


class FleetOverview(_Dto):
    """``GET /admin/fleet/overview``:一次性聚合 fleet 健康 + 服务能力 + 内部互联网格。

    供 Web 仪表盘单次拉取(取代逐节点 N 次调用):``summary`` 同 ``GET /admin/health``;
    ``nodes`` 是 fleet 健康行 + ``capabilities``;``links`` 是全网去重的物理 WG 邻接边。
    """

    summary: dict[NodeHealth, int]
    nodes: list[FleetOverviewNode]
    links: list[FleetLink]


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


# ---- WebUI 专用观测聚合视图 ----


class TrafficPoint(_Dto):
    """一个时间点的吞吐率(字节/秒),由相邻快照的 WG 累计计数差分得出。"""

    captured_at: str | None = None
    rx_bytes_per_sec: float = 0
    tx_bytes_per_sec: float = 0


class NodeTraffic(_Dto):
    """``GET /admin/nodes/{id}/traffic``:单节点 WG 吞吐时间线(服务端差分,免前端拉全量快照)。"""

    node_id: str
    points: list[TrafficPoint] = []


class FleetTraffic(_Dto):
    """``GET /admin/fleet/traffic``:全 fleet 吞吐时间线(各节点按时间桶对齐求和)。"""

    points: list[TrafficPoint] = []


class LinkStatus(_Dto):
    """一条链路(目前 WireGuard)的 per-peer 运行时状态(服务端已判 up/stale/down)。"""

    interface: str | None = None
    type: str = "wireguard"
    public_key: str | None = None
    endpoint: str | None = None
    last_handshake_seconds: int | None = None
    transfer_rx_bytes: int = 0
    transfer_tx_bytes: int = 0
    status: str


class NodeLinks(_Dto):
    """``GET /admin/nodes/{id}/links``:单节点全部链路状态(类型化,免前端扒 last_snapshot)。"""

    node_id: str
    links: list[LinkStatus] = []


class BgpSessionStatus(_Dto):
    """一条 BGP 会话的状态:范围(内/外)+ Established 判定,服务端按配置归类。"""

    name: str | None = None
    session: str | None = None
    scope: str
    state: str | None = None
    health: str
    since: str | None = None
    info: str | None = None


class NodeBgpSessions(_Dto):
    """``GET /admin/nodes/{id}/bgp-sessions/status``:内 iBGP + 外 eBGP 综合状态。"""

    node_id: str
    sessions: list[BgpSessionStatus] = []


class NodeOverview(NodeHealthRow):
    """``GET /admin/nodes/{id}/overview``:节点页一次拉全,免前端 4× 拉 health + 扒 last_snapshot。

    一个请求把节点概览页 + 链路/BGP 状态列需要的都给齐:健康行(继承)+ 服务能力 +
    进程自观测 + 当前 drift(取自 last_report)+ 类型化链路状态 + BGP 会话状态。历史
    序列(sparkline/趋势)仍走 status-events,不在此聚合。
    """

    capabilities: list[str] = []
    self_metrics: dict[str, Any] | None = None
    drift: list[dict[str, Any]] = []
    links: list[LinkStatus] = []
    bgp_sessions: list[BgpSessionStatus] = []


__all__ = [
    "BgpSessionStatus",
    "FleetHealth",
    "FleetLink",
    "FleetOverview",
    "FleetOverviewNode",
    "FleetTraffic",
    "LinkStatus",
    "NodeBgpSessions",
    "NodeHealthDetail",
    "NodeHealthRow",
    "NodeLinks",
    "NodeOverview",
    "NodeStatusEvents",
    "NodeTraffic",
    "StatusEvent",
]
