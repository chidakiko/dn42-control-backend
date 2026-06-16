from __future__ import annotations

"""管理面路由全表视图的响应 DTO。

agent↔server 的核心协议（``RoutingTableSnapshot``）在 dn42_schemas；这里是控制面
聚合后对外暴露的只读查询契约（挂 ``response_model``）。聚合内部结构（直方图、
分布）用宽松的 dict 表达，避免在 DTO 层重复定义图表细节。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RpkiCounts(_Dto):
    # RPKI 路由起源校验只有三个真实状态(RFC6811)。「未知」已移除——
    # 无法判定(多半 ROA 表没采到)由 RoutingSummary.rpki_observed 表达。
    valid: int = 0
    invalid: int = 0
    not_found: int = 0


class PrefilterPeer(_Dto):
    """过滤前(import-table)单对端统计。"""

    protocol: str
    remote_asn: int | None = None
    received: int = 0
    accepted: int = 0
    valid: int = 0
    invalid: int = 0
    not_found: int = 0


class PrefilterRoute(_Dto):
    """过滤前一条被拒(无效)路由的最小标识。"""

    prefix: str
    origin_asn: int | None = None
    protocol: str
    # 被拒首要原因(仅 filtered_routes):out_of_range/self_net/as_path_too_long/blocked_asn/policy。
    reason: str | None = None


class PrefilterRpki(_Dto):
    """过滤前 RPKI 分布(import-table 聚合) + per-peer 明细 + 无效/被策略过滤路由清单。"""

    received: int = 0
    accepted: int = 0
    valid: int = 0
    invalid: int = 0
    not_found: int = 0
    peers: list[PrefilterPeer] = []
    invalid_routes: list[PrefilterRoute] = []
    # 被 import 过滤器主动拒绝、非 RPKI 无效的路由（bogon / 前缀长度 / 策略等）。
    filtered_routes: list[PrefilterRoute] = []


class RoutingSummary(_Dto):
    """``GET /admin/nodes/{id}/routing/summary``：全表规模 + 分布。"""

    node_id: str
    observation: str
    captured_at: str | None = None
    updated_at: str | None = None
    route_count: int = 0
    route_count_v4: int = 0
    route_count_v6: int = 0
    local_count: int = 0
    rpki: RpkiCounts = RpkiCounts()
    # ROA 表是否采到:False ⇒ RPKI 校验不可用(前端显式提示),rpki 计数此时全 0。
    rpki_observed: bool = True
    # {"4": {"24": n, ...}, "6": {"48": n, ...}}
    prefix_lengths: dict[str, dict[str, int]] = {}
    # {"<as_path_len>": n}
    as_path_lengths: dict[str, int] = {}
    peers: list[dict[str, Any]] = []
    # 过滤前(import-table)RPKI 分布;旧 agent / 采集失败为 None。
    prefilter: PrefilterRpki | None = None


class FleetRoutingNode(_Dto):
    node_id: str
    observation: str
    captured_at: str | None = None
    route_count: int = 0
    route_count_v4: int = 0
    route_count_v6: int = 0
    rpki: RpkiCounts = RpkiCounts()


class FleetRoutingSummary(_Dto):
    route_count: int = 0
    route_count_v4: int = 0
    route_count_v6: int = 0
    rpki: RpkiCounts = RpkiCounts()
    nodes_reporting: int = 0


class FleetRouting(_Dto):
    """``GET /admin/routing/fleet``：跨节点的路由总览。"""

    summary: FleetRoutingSummary
    nodes: list[FleetRoutingNode]


class OriginEntry(_Dto):
    asn: int
    count: int


class RoutingOrigins(_Dto):
    """``GET /admin/nodes/{id}/routing/origins``：起源 AS Top 榜。"""

    node_id: str
    total: int
    origins: list[OriginEntry]


class RoutingPrefixes(_Dto):
    """``GET /admin/nodes/{id}/routing/prefixes``：分页 / 过滤后的路由列表。"""

    node_id: str
    total: int
    limit: int
    offset: int
    routes: list[dict[str, Any]]


class RoutingTimelineEvent(_Dto):
    id: int
    captured_at: str | None = None
    created_at: str | None = None
    route_count: int = 0
    route_count_v4: int = 0
    route_count_v6: int = 0
    rpki: RpkiCounts = RpkiCounts()
    announced: int = 0
    withdrawn: int = 0


class RoutingTimeline(_Dto):
    """``GET /admin/nodes/{id}/routing/timeline``：路由表时间序列。"""

    node_id: str
    events: list[RoutingTimelineEvent]


class IbgpPeerView(_Dto):
    """单个 iBGP 对端（由 internal_topology 推导，含路由层 liveness 关联）。

    iBGP 不是 ``bgp_sessions`` 记录，而是 ``bird.internal_topology`` 在渲染时
    自动合成的 loopback 全互联，所以"BGP 会话"面板看不到它——这个视图专门把它
    暴露出来。``rib_routes`` 是该 iBGP 协议在本节点 RIB 里贡献的最优路由条数
    （来自路由快照聚合的 per-peer 计数），``in_rib`` 即 ``rib_routes > 0``。
    这是**强 liveness 信号**（有路由必然会话已起），但不是 BGP 会话状态机的
    权威 Established——某协议会话已起却 0 条最优路由时不会出现在快照里。
    """

    node: str
    ownip: str
    ownip6: str
    protocol: str
    rib_routes: int = 0
    in_rib: bool = False


class OspfNeighborView(_Dto):
    """OSPF 邻接（由 internal_topology.igp_adjacencies 推导）。"""

    node: str
    interface: str | None = None
    cost: int | None = None
    iface_type: str = "ptp"


class OspfProtocolView(_Dto):
    """单个 OSPF 协议（int_ospf / int_ospf_v6）的路由层 liveness 关联。"""

    protocol: str
    rib_routes: int = 0
    in_rib: bool = False


class InternalTopologyView(_Dto):
    """``GET /admin/nodes/{id}/internal-topology``：iBGP + OSPF 内部互联视图。

    把 ``bird.internal_topology``（配置）与路由快照的 per-peer 计数（实时关联）
    合在一起，供 UI 渲染一个独立的"内部互联 / iBGP+OSPF"面板。``configured``
    为 ``False`` 表示该节点未配置内部拓扑（无 iBGP/OSPF）。
    """

    node_id: str
    configured: bool = False
    full_mesh_ibgp: bool = True
    ospf_v2: bool = True
    ospf_v3: bool = True
    routers: list[str] = []
    ibgp_peers: list[IbgpPeerView] = []
    ospf_neighbors: list[OspfNeighborView] = []
    ospf: list[OspfProtocolView] = []
    # 路由层 liveness 的可信度参照：是否有可关联的已观测路由快照，及其采集时刻。
    routing_observed: bool = False
    captured_at: str | None = None


__all__ = [
    "FleetRouting",
    "FleetRoutingNode",
    "FleetRoutingSummary",
    "IbgpPeerView",
    "InternalTopologyView",
    "OriginEntry",
    "OspfNeighborView",
    "OspfProtocolView",
    "RoutingOrigins",
    "RoutingPrefixes",
    "RoutingSummary",
    "RoutingTimeline",
    "RoutingTimelineEvent",
    "RpkiCounts",
]
