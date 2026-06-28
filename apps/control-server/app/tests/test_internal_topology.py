from __future__ import annotations

"""iBGP + OSPF 内部互联视图：纯 builder 单测 + ``/internal-topology`` 端点闭环。

iBGP/OSPF 不是 ``bgp_sessions`` 记录（"BGP 会话"面板看不到），由
``bird.internal_topology`` 自动合成。本视图把配置态与路由快照的 per-peer 计数
关联起来，供 UI 单列一个"内部互联"面板。
"""

from fastapi.testclient import TestClient

from dn42_schemas import BirdHostSpec, IgpAdjacencySpec, InternalTopologySpec

from app.core.config import ControlServerConfig
from app.services.internal_topology import build_internal_topology_view


def _topology() -> InternalTopologySpec:
    return InternalTopologySpec(
        routers=["pvg2", "can2"],
        hosts={
            "pvg2": BirdHostSpec(ownip="172.20.0.59", ownip6="fdce:1111:2222:9500::59"),
            "can2": BirdHostSpec(ownip="172.20.0.55", ownip6="fdce:1111:2222:9500::55"),
        },
        igp_adjacencies=[IgpAdjacencySpec(node="can2", cost=100, interface="wg-can2")],
    )


# -------- 纯 builder --------

def test_builder_unconfigured_topology() -> None:
    view = build_internal_topology_view("pvg2", None, None)
    assert view == {
        "node_id": "pvg2",
        "configured": False,
        "routing_observed": False,
        "captured_at": None,
    }


def test_builder_configured_without_routing_snapshot() -> None:
    view = build_internal_topology_view("pvg2", _topology(), None)

    assert view["configured"] is True
    assert view["routers"] == ["pvg2", "can2"]
    assert view["routing_observed"] is False
    # 自己不出现在 iBGP 对端里；对端协议名与 ibgp.conf.j2 对齐（- -> _）。
    assert len(view["ibgp_peers"]) == 1
    peer = view["ibgp_peers"][0]
    assert peer["node"] == "can2"
    assert peer["protocol"] == "ibgp_can2"
    assert peer["ownip6"] == "fdce:1111:2222:9500::55"
    assert peer["rib_routes"] == 0 and peer["in_rib"] is False
    # OSPF 两协议默认都在；邻接接口取 igp_adjacency.interface。
    assert [o["protocol"] for o in view["ospf"]] == ["int_ospf", "int_ospf_v6"]
    assert view["ospf_neighbors"][0]["interface"] == "wg-can2"
    assert view["ospf_neighbors"][0]["cost"] == 100


def test_builder_correlates_routing_peer_counts() -> None:
    summary = {
        "observation": "observed",
        "captured_at": "2026-01-01T00:00:00",
        "peers": [
            {"protocol": "ibgp_can2", "count": 287},
            {"protocol": "int_ospf", "count": 4},
            {"protocol": "int_ospf_v6", "count": 4},
            {"protocol": "leziblog_x_v6_v4", "count": 100},  # eBGP，不应混入
        ],
    }
    view = build_internal_topology_view("pvg2", _topology(), summary)

    assert view["routing_observed"] is True
    assert view["captured_at"] == "2026-01-01T00:00:00"
    peer = view["ibgp_peers"][0]
    assert peer["rib_routes"] == 287 and peer["in_rib"] is True
    ospf = {o["protocol"]: o for o in view["ospf"]}
    assert ospf["int_ospf"]["rib_routes"] == 4 and ospf["int_ospf"]["in_rib"] is True


def test_builder_omits_disabled_ospf_family() -> None:
    topology = _topology().model_copy(update={"ospf_v3": False})
    view = build_internal_topology_view("pvg2", topology, None)
    assert [o["protocol"] for o in view["ospf"]] == ["int_ospf"]


# -------- HTTP 端点闭环 --------

def test_internal_topology_endpoint_and_liveness(
    client: TestClient, config: ControlServerConfig
) -> None:
    node = config.bootstrap_node_id  # edge1，seed 自带 internal_topology
    token = config.bootstrap_agent_token

    view = client.get(f"/api/v1/ui/nodes/{node}/internal-topology")
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["configured"] is True
    assert node in body["routers"]
    peers = {p["node"]: p for p in body["ibgp_peers"]}
    assert "edge2" in peers
    assert peers["edge2"]["protocol"] == "ibgp_edge2"
    assert peers["edge2"]["in_rib"] is False  # 还没上报路由
    assert body["routing_observed"] is False

    # 上报一条由该 iBGP 协议贡献的最优路由 -> liveness 关联点亮。
    payload = {
        "node_id": node,
        "captured_at": "2026-01-01T00:00:00Z",
        "observation": "observed",
        "routes": [
            {
                "prefix": "172.20.0.0/24",
                "origin_asn": 64500,
                "as_path": [64500],
                "next_hop": None,
                "protocol": "ibgp_edge2",
                "primary": True,
                "local": False,
                "rpki": "valid",
            }
        ],
    }
    posted = client.post(
        "/api/v1/agent/routing-table",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert posted.status_code == 200, posted.text

    body2 = client.get(f"/api/v1/ui/nodes/{node}/internal-topology").json()
    assert body2["routing_observed"] is True
    peers2 = {p["node"]: p for p in body2["ibgp_peers"]}
    assert peers2["edge2"]["in_rib"] is True
    assert peers2["edge2"]["rib_routes"] == 1


def test_internal_topology_404_for_unknown_node(client: TestClient) -> None:
    r = client.get("/api/v1/ui/nodes/does-not-exist/internal-topology")
    assert r.status_code == 404
