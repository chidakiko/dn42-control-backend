from __future__ import annotations

"""把节点的 ``bird.internal_topology`` 折叠成对外的 iBGP+OSPF 视图（纯函数）。

iBGP 与 OSPF 不是 ``bgp_sessions`` 记录，而是 ``DesiredState.bird.internal_topology``
在渲染时由 ``ibgp.conf.j2`` / ``ospf*.conf.j2`` 自动合成，所以"BGP 会话"面板看不到
它们。这里把**配置态**（routers / loopback / IGP 邻接 / 开关）与**路由层 liveness**
（路由快照聚合的 per-peer 计数）关联起来，供 UI 渲染独立的"内部互联"面板。

liveness 口径：``rib_routes`` 是该协议在本节点 RIB 里贡献的**最优路由条数**
（路由快照 ``peers`` 聚合）。有路由 ⇒ 会话必然已起（强信号）；但 0 条最优路由的
已起会话不会出现在快照里，所以这是 liveness 提示，不是 BGP 状态机的权威 Established。
保持纯函数、无 DB，便于单测。
"""

from dn42_schemas import InternalTopologySpec


def _ibgp_protocol_name(host: str) -> str:
    """与 ``config-bird2/ibgp.conf.j2`` 的 ``ibgp_{{ host | replace("-","_") }}`` 对齐。"""

    return f"ibgp_{host.replace('-', '_')}"


def build_internal_topology_view(
    node_id: str,
    topology: InternalTopologySpec | None,
    summary: dict | None,
) -> dict:
    """组装 ``/internal-topology`` 响应。

    Args:
        node_id: 目标节点。
        topology: 该节点 DesiredState 的 ``bird.internal_topology``；``None`` 表示未配置。
        summary: ``RoutingStore.get_summary(node_id)`` 的返回（用于关联 per-peer 路由数）；
            没有路由快照时为 ``None``。
    """

    peer_routes: dict[str, int] = {}
    routing_observed = False
    captured_at: str | None = None
    if summary is not None:
        for entry in summary.get("peers", []) or []:
            protocol = entry.get("protocol")
            if protocol:
                peer_routes[protocol] = int(entry.get("count", 0) or 0)
        routing_observed = summary.get("observation") == "observed"
        captured_at = summary.get("captured_at")

    base: dict = {
        "node_id": node_id,
        "configured": topology is not None,
        "routing_observed": routing_observed,
        "captured_at": captured_at,
    }
    if topology is None:
        return base

    ibgp_peers: list[dict] = []
    for host in topology.routers:
        if host == node_id:
            continue
        spec = topology.hosts.get(host)
        if spec is None:
            continue
        protocol = _ibgp_protocol_name(host)
        count = peer_routes.get(protocol, 0)
        ibgp_peers.append(
            {
                "node": host,
                "ownip": spec.ownip,
                "ownip6": spec.ownip6,
                "protocol": protocol,
                "rib_routes": count,
                "in_rib": count > 0,
            }
        )

    ospf_neighbors = [
        {
            "node": adjacency.node,
            "interface": adjacency.interface or f"igp-{adjacency.node}",
            "cost": adjacency.cost,
            "iface_type": adjacency.iface_type,
        }
        for adjacency in topology.igp_adjacencies
    ]

    ospf: list[dict] = []
    for enabled, protocol in (
        (topology.ospf_v2, "int_ospf"),
        (topology.ospf_v3, "int_ospf_v6"),
    ):
        if not enabled:
            continue
        count = peer_routes.get(protocol, 0)
        ospf.append({"protocol": protocol, "rib_routes": count, "in_rib": count > 0})

    base.update(
        {
            "full_mesh_ibgp": topology.full_mesh_ibgp,
            "ospf_v2": topology.ospf_v2,
            "ospf_v3": topology.ospf_v3,
            "routers": list(topology.routers),
            "ibgp_peers": ibgp_peers,
            "ospf_neighbors": ospf_neighbors,
            "ospf": ospf,
        }
    )
    return base


__all__ = ["build_internal_topology_view"]
