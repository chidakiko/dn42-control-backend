from __future__ import annotations

"""路由全表上报 / 聚合 / 查询的测试。

覆盖：
- 纯聚合函数 ``aggregate_routes`` 的各分布口径（去重、v4/v6、RPKI、起源、前缀长度）。
- ``POST /agent/routing-table`` 上报 → ``/admin/nodes/{id}/routing/*`` 查询闭环。
- churn：两次快照间的 announced / withdrawn。
- 非 OBSERVED 上报不清空已有全表。
"""

from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig
from app.services.routing_aggregate import aggregate_routes, diff_prefix_sets


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _route(
    prefix: str,
    origin: int | None,
    *,
    as_path=None,
    rpki=None,
    protocol="bgp_a",
    primary=True,
    local=False,
) -> dict:
    return {
        "prefix": prefix,
        "origin_asn": origin,
        "as_path": as_path if as_path is not None else ([origin] if origin else []),
        "next_hop": None,
        "protocol": protocol,
        "primary": primary,
        "local": local,
        "rpki": rpki,
    }


# -------- 纯聚合 --------

def test_aggregate_counts_families_and_dedups_multipath() -> None:
    routes = [
        _route("172.20.0.0/24", 64500, rpki="valid"),
        # 同前缀次优路由：去重后只算一次
        _route("172.20.0.0/24", 64501, primary=False, protocol="bgp_b"),
        _route("10.0.0.0/8", 64500, rpki="invalid"),
        _route("fd42:1::/48", 64600, as_path=[64600, 64601], rpki=None),
    ]
    agg = aggregate_routes(routes)

    assert agg["route_count"] == 3
    assert agg["route_count_v4"] == 2
    assert agg["route_count_v6"] == 1
    # 「未知」状态已移除：rpki=None 的那条不计入任何桶（只剩三态真实状态）。
    assert agg["rpki"] == {"valid": 1, "invalid": 1, "not_found": 0}
    assert agg["rpki_observed"] is True  # 至少分类出一条 ⇒ ROA 采到了
    # 起源按计数降序：64500 出现两次（去重后的最优路径）
    assert agg["origins"][0] == {"asn": 64500, "count": 2}
    assert agg["prefix_lengths"]["4"] == {"8": 1, "24": 1}
    assert agg["prefix_lengths"]["6"] == {"48": 1}
    assert agg["as_path_lengths"]["2"] == 1


def test_aggregate_rpki_observed_false_when_roa_table_missing() -> None:
    """整张 ROA 表没采到 ⇒ 外部路由全 rpki=None ⇒ rpki_observed=False、计数全 0。"""

    routes = [
        _route("172.20.0.0/24", 64500, rpki=None),
        _route("10.0.0.0/8", 64501, rpki=None),
        _route("172.20.0.62/32", None, as_path=[], local=True),  # 本地,不参与
    ]
    agg = aggregate_routes(routes)
    assert agg["rpki"] == {"valid": 0, "invalid": 0, "not_found": 0}
    assert agg["rpki_observed"] is False
    assert agg["local_count"] == 1


def test_diff_prefix_sets() -> None:
    announced, withdrawn = diff_prefix_sets({"a", "b"}, {"b", "c", "d"})
    assert announced == 2  # c, d
    assert withdrawn == 1  # a


# -------- 上报 + 查询闭环 --------

def _post_routes(client: TestClient, node: str, token: str, routes: list[dict], captured: str) -> None:
    payload = {
        "node_id": node,
        "captured_at": captured,
        "observation": "observed",
        "routes": routes,
    }
    r = client.post("/api/v1/agent/routing-table", headers=_auth(token), json=payload)
    assert r.status_code == 200, r.text


def test_routing_ingest_and_query(client: TestClient, config: ControlServerConfig) -> None:
    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token

    routes = [
        _route("172.20.0.0/24", 64500, rpki="valid"),
        _route("172.21.0.0/24", 64500, rpki="not-found"),
        _route("fd42:1::/48", 64600, as_path=[64600], rpki="invalid"),
    ]
    _post_routes(client, node, token, routes, "2025-01-01T00:00:00Z")

    summary = client.get(f"/api/v1/admin/nodes/{node}/routing/summary").json()
    assert summary["route_count"] == 3
    assert summary["route_count_v4"] == 2
    assert summary["route_count_v6"] == 1
    assert summary["rpki"]["valid"] == 1
    assert summary["rpki"]["not_found"] == 1
    assert summary["observation"] == "observed"

    origins = client.get(f"/api/v1/admin/nodes/{node}/routing/origins").json()
    assert origins["total"] == 2
    assert origins["origins"][0] == {"asn": 64500, "count": 2}

    # 前缀检索 + family 过滤
    v6 = client.get(
        f"/api/v1/admin/nodes/{node}/routing/prefixes", params={"family": "6"}
    ).json()
    assert v6["total"] == 1
    assert v6["routes"][0]["prefix"] == "fd42:1::/48"

    search = client.get(
        f"/api/v1/admin/nodes/{node}/routing/prefixes", params={"q": "172.20"}
    ).json()
    assert search["total"] == 1
    assert search["routes"][0]["prefix"] == "172.20.0.0/24"


def test_routing_local_count_and_scope_filter(
    client: TestClient, config: ControlServerConfig
) -> None:
    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token

    routes = [
        _route("172.20.0.0/26", 64500, rpki="valid"),  # external (eBGP)
        _route("172.20.1.0/26", 64500, as_path=[], protocol="static_routes4", local=True),
        _route("fdce:3333:4444::/48", 64500, as_path=[], protocol="static_routes6", local=True),
    ]
    _post_routes(client, node, token, routes, "2025-01-01T00:00:00Z")

    summary = client.get(f"/api/v1/admin/nodes/{node}/routing/summary").json()
    assert summary["local_count"] == 2
    # 本地路由不参与 RPKI 分布：只有 1 条外部路由计入。
    assert sum(summary["rpki"].values()) == 1
    assert summary["rpki"]["valid"] == 1

    only_local = client.get(
        f"/api/v1/admin/nodes/{node}/routing/prefixes", params={"scope": "local"}
    ).json()
    assert only_local["total"] == 2
    assert all(r["local"] for r in only_local["routes"])

    only_external = client.get(
        f"/api/v1/admin/nodes/{node}/routing/prefixes", params={"scope": "external"}
    ).json()
    assert only_external["total"] == 1
    assert only_external["routes"][0]["prefix"] == "172.20.0.0/26"


def test_routing_timeline_churn(client: TestClient, config: ControlServerConfig) -> None:
    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token

    _post_routes(
        client, node, token,
        [_route("172.20.0.0/24", 64500), _route("10.0.0.0/8", 64500)],
        "2025-01-01T00:00:00Z",
    )
    # 第二次：撤销 10.0.0.0/8，新增 172.21.0.0/24
    _post_routes(
        client, node, token,
        [_route("172.20.0.0/24", 64500), _route("172.21.0.0/24", 64501)],
        "2025-01-01T00:05:00Z",
    )

    timeline = client.get(f"/api/v1/admin/nodes/{node}/routing/timeline").json()
    events = timeline["events"]
    assert len(events) == 2
    # 时间升序：第二条带 churn
    assert events[-1]["announced"] == 1
    assert events[-1]["withdrawn"] == 1
    assert events[-1]["route_count"] == 2


def test_unavailable_report_keeps_previous_table(
    client: TestClient, config: ControlServerConfig
) -> None:
    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token

    _post_routes(client, node, token, [_route("172.20.0.0/24", 64500)], "2025-01-01T00:00:00Z")

    # 采集失败上报：不应清空已有全表，只更新 observation。
    r = client.post(
        "/api/v1/agent/routing-table",
        headers=_auth(token),
        json={
            "node_id": node,
            "captured_at": "2025-01-01T00:10:00Z",
            "observation": "unavailable",
            "routes": [],
        },
    )
    assert r.status_code == 200

    summary = client.get(f"/api/v1/admin/nodes/{node}/routing/summary").json()
    assert summary["observation"] == "unavailable"
    assert summary["route_count"] == 1  # 旧数据仍在


def test_routing_fleet_aggregates_across_nodes(
    client: TestClient, config: ControlServerConfig
) -> None:
    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token

    # fleet 在无上报时也应返回空总览（不 404）。
    empty = client.get("/api/v1/admin/routing/fleet").json()
    assert empty["summary"]["nodes_reporting"] == 0
    assert empty["nodes"] == []

    _post_routes(
        client, node, token,
        [
            _route("172.20.0.0/24", 64500, rpki="valid"),
            _route("fd42:1::/48", 64600, as_path=[64600], rpki="invalid"),
        ],
        "2025-01-01T00:00:00Z",
    )

    fleet = client.get("/api/v1/admin/routing/fleet").json()
    assert fleet["summary"]["route_count"] == 2
    assert fleet["summary"]["route_count_v4"] == 1
    assert fleet["summary"]["route_count_v6"] == 1
    assert fleet["summary"]["rpki"]["valid"] == 1
    assert fleet["summary"]["nodes_reporting"] == 1
    assert any(n["node_id"] == node and n["route_count"] == 2 for n in fleet["nodes"])


def test_routing_summary_404_when_never_reported(client: TestClient) -> None:
    assert (
        client.get("/api/v1/admin/nodes/edge1/routing/summary").status_code == 404
    )


def test_routing_table_rejects_other_node(client: TestClient, config: ControlServerConfig) -> None:
    token = config.bootstrap_agent_token
    r = client.post(
        "/api/v1/agent/routing-table",
        headers=_auth(token),
        json={
            "node_id": "someone-else",
            "captured_at": "2025-01-01T00:00:00Z",
            "observation": "observed",
            "routes": [],
        },
    )
    assert r.status_code == 403


def test_routing_prefilter_ingest_and_summary(
    client: TestClient, config: ControlServerConfig
) -> None:
    """agent 上报的过滤前(import-table)分布存进 aggregates 并在 summary 暴露。"""

    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token
    prefilter = {
        "received": 10,
        "accepted": 8,
        "valid": 8,
        "invalid": 1,
        "not_found": 1,
        "peers": [
            {
                "protocol": "demopeer_v4",
                "remote_asn": 4242420001,
                "received": 6,
                "accepted": 5,
                "valid": 5,
                "invalid": 1,
                "not_found": 0,
            },
            {
                "protocol": "sess_v4",
                "remote_asn": 4242422466,
                "received": 4,
                "accepted": 3,
                "valid": 3,
                "invalid": 0,
                "not_found": 1,
            },
        ],
    }
    payload = {
        "node_id": node,
        "captured_at": "2025-02-01T00:00:00Z",
        "observation": "observed",
        "routes": [_route("172.20.0.0/24", 64500, rpki="valid")],
        "prefilter": prefilter,
    }
    r = client.post("/api/v1/agent/routing-table", headers=_auth(token), json=payload)
    assert r.status_code == 200, r.text

    summary = client.get(f"/api/v1/admin/nodes/{node}/routing/summary").json()
    pf = summary["prefilter"]
    assert pf is not None
    assert pf["received"] == 10 and pf["accepted"] == 8
    assert pf["invalid"] == 1 and pf["not_found"] == 1
    assert [p["protocol"] for p in pf["peers"]] == ["demopeer_v4", "sess_v4"]
    assert pf["peers"][0]["invalid"] == 1


def test_routing_summary_prefilter_none_for_legacy_agent(
    client: TestClient, config: ControlServerConfig
) -> None:
    """旧 agent 不带 prefilter:summary.prefilter 为 None,不报错。"""

    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token
    _post_routes(client, node, token, [_route("10.0.0.0/8", 64500)], "2025-03-01T00:00:00Z")
    summary = client.get(f"/api/v1/admin/nodes/{node}/routing/summary").json()
    assert summary["prefilter"] is None
