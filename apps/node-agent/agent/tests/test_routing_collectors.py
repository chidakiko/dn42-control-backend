from __future__ import annotations

"""路由全表采集器的单元测试。

锁定 BIRD ``show route all`` 解析、ROA / RPKI 起源校验，以及三态观测语义
（未采集 / 采集失败 / 已采集）。全部纯函数 + 注入式 runner，不碰真实 BIRD。
"""

from ipaddress import ip_network
from types import SimpleNamespace

from dn42_schemas import ObservationStatus
from dn42_schemas.testing import build_hkg1_example_state

from agent.collectors.routing import (
    RejectPolicy,
    RouteTableObserver,
    RpkiIndex,
    aggregate_prefilter,
    build_routing_observer,
    classify_reject_reason,
    collect_routing_snapshot,
    parse_bird_routes,
    parse_ebgp_protocol_names,
)

_ROUTES_V4 = "\n".join(
    [
        "Table master4:",
        "172.20.0.0/24        unicast [bgp_demopeer_v4 2024-06-01 10:00:00] * (100) [AS4242420000i]",
        "\tvia 172.20.53.1 on eth0",
        "\tType: BGP univ",
        "\tBGP.origin: IGP",
        "\tBGP.as_path: 4242420001 4242420000",
        "\tBGP.next_hop: 172.20.53.1",
        "10.0.0.0/8           unicast [bgp_a 2024-06-01 10:00:00] * (100) [AS64500i]",
        "\tvia 10.1.1.1 on eth0",
        "\tBGP.as_path: 4242420001 64500",
        "                     unicast [bgp_b 2024-06-01 10:00:00]   (90) [AS64501i]",
        "\tvia 10.2.2.2 on eth1",
        "\tBGP.as_path: 4242420001 64600 64501",
    ]
)

_ROUTES_V6 = "\n".join(
    [
        "Table master6:",
        "fd42:1::/48          unicast [bgp_v6 2024-06-01 10:00:00] * (100) [AS4242420000i]",
        "\tvia fe80::1 on eth0",
        "\tBGP.as_path: 4242420001 4242420000",
    ]
)

_ROA = "\n".join(
    [
        "Table roa4:",
        "172.20.0.0/24-24 AS4242420000",
        "172.20.0.0/16-24 AS4242420000",
        "10.0.0.0/8-24 AS64500",
    ]
)


def test_parse_routes_extracts_fields_and_origin() -> None:
    routes = parse_bird_routes(_ROUTES_V4)
    assert [r.prefix for r in routes] == ["172.20.0.0/24", "10.0.0.0/8", "10.0.0.0/8"]

    first = routes[0]
    assert first.protocol == "bgp_demopeer_v4"
    assert first.primary is True
    assert first.next_hop == "172.20.53.1"
    assert first.as_path == [4242420001, 4242420000]
    assert first.origin_asn == 4242420000  # AS path 末位


def test_parse_routes_handles_multipath_same_prefix() -> None:
    routes = parse_bird_routes(_ROUTES_V4)
    # 同前缀两条：第一条最优（*），第二条非最优，前缀从上一条继承。
    multipath = [r for r in routes if r.prefix == "10.0.0.0/8"]
    assert len(multipath) == 2
    assert multipath[0].primary is True
    assert multipath[1].primary is False
    assert multipath[1].origin_asn == 64501
    assert multipath[1].next_hop == "10.2.2.2"


_ROUTES_WITH_COMMUNITIES = "\n".join(
    [
        "Table master4:",
        "172.20.0.0/24        unicast [bgp_a 2024-06-01 10:00:00] * (100) [AS4242420000i]",
        "\tvia 172.20.53.1 on eth0",
        "\tBGP.as_path: 4242420001 4242420000",
        "\tBGP.community: (64511,1) (64511,2)",
        "\tBGP.large_community: (4242420000, 1, 31) (4242420000, 2, 41)",
    ]
)


def test_parse_routes_extracts_communities() -> None:
    routes = parse_bird_routes(_ROUTES_WITH_COMMUNITIES)
    route = routes[0]
    assert route.communities == ["64511:1", "64511:2"]
    assert route.large_communities == ["4242420000:1:31", "4242420000:2:41"]


def test_parse_routes_handles_ipv6() -> None:
    routes = parse_bird_routes(_ROUTES_V6)
    assert len(routes) == 1
    assert routes[0].prefix == "fd42:1::/48"
    assert routes[0].next_hop == "fe80::1"


def test_rpki_index_classifies_origin_validation() -> None:
    index = RpkiIndex.from_bird(_ROA)

    # 起源匹配 + 前缀长度未超 maxlen → valid
    assert index.classify("172.20.0.0/24", 4242420000) == "valid"
    # 被 /16-24 覆盖，但起源 AS 不符 → invalid
    assert index.classify("172.20.5.0/24", 9999) == "invalid"
    # 无任何 ROA 覆盖 → not-found
    assert index.classify("203.0.113.0/24", 64500) == "not-found"
    # 覆盖但拿不到起源 → 无法判定,返回 None（不参与统计）
    assert index.classify("172.20.0.0/24", None) is None


def test_observer_returns_none_without_runner() -> None:
    assert RouteTableObserver().observe() is None


def test_observer_returns_none_on_collection_failure() -> None:
    assert RouteTableObserver(command_runner=lambda: None).observe() is None


def test_observer_returns_empty_on_empty_output() -> None:
    assert RouteTableObserver(command_runner=lambda: "").observe() == []


def test_observer_attaches_rpki_when_roa_runner_present() -> None:
    observer = RouteTableObserver(
        command_runner=lambda: _ROUTES_V4,
        roa_runner=lambda: _ROA,
    )
    routes = observer.observe()
    assert routes is not None
    by_prefix = {r.prefix: r for r in routes}
    assert by_prefix["172.20.0.0/24"].rpki == "valid"


_LOCAL_STATIC = "\n".join(
    [
        "Table master4:",
        "172.20.0.0/24        unreachable [static1 2024-06-01 10:00:00] * (200)",
        "\tType: static univ",
    ]
)


def test_observer_marks_local_routes_without_rpki_or_origin_rewrite() -> None:
    # 本地静态路由（无 AS path）：只打 local 标签，不参与 RPKI、不改写起源。
    # 即使提供了 ROA，本地路由的 rpki 仍为 None，origin 保持解析原值（空）。
    observer = RouteTableObserver(command_runner=lambda: _LOCAL_STATIC, roa_runner=lambda: _ROA)
    routes = observer.observe()
    assert routes is not None
    route = routes[0]
    assert route.local is True
    assert route.as_path == []
    assert route.origin_asn is None  # 不改写起源
    assert route.rpki is None  # 不参与 RPKI


def test_observer_leaves_rpki_none_without_roa() -> None:
    observer = RouteTableObserver(command_runner=lambda: _ROUTES_V4)
    routes = observer.observe()
    assert routes is not None
    assert all(r.rpki is None for r in routes)


class _FakeExec:
    """按 argv 返回预置 (rc, stdout, stderr)；未命中视为命令失败。"""

    def __init__(self, outputs: dict[tuple[str, ...], tuple[int, str, str]]) -> None:
        self._outputs = outputs

    def run(self, container: str, argv: list[str]) -> tuple[int, str, str]:
        return self._outputs.get(tuple(argv), (1, "", "not found"))

    def put_file(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - 未用
        raise NotImplementedError


def test_collect_routing_snapshot_observed() -> None:
    state = build_hkg1_example_state()
    exec_ = _FakeExec(
        {
            ("birdc", "show", "route", "table", "master4", "all"): (0, _ROUTES_V4, ""),
            ("birdc", "show", "route", "table", "master6", "all"): (0, _ROUTES_V6, ""),
            ("birdc", "show", "route", "table", "dn42_roa"): (0, _ROA, ""),
        }
    )
    snapshot = collect_routing_snapshot(state, exec_, captured_at="2024-06-01T10:00:00+00:00")

    assert snapshot.observation == ObservationStatus.OBSERVED
    prefixes = {r.prefix for r in snapshot.routes}
    assert "172.20.0.0/24" in prefixes
    assert "fd42:1::/48" in prefixes
    by_prefix = {r.prefix: r for r in snapshot.routes}
    assert by_prefix["172.20.0.0/24"].rpki == "valid"
    assert by_prefix["172.20.0.0/24"].local is False  # eBGP 学来，非本地起源


def test_collect_routing_snapshot_unavailable_when_all_commands_fail() -> None:
    state = build_hkg1_example_state()
    snapshot = collect_routing_snapshot(state, _FakeExec({}), captured_at="2024-06-01T10:00:00+00:00")
    assert snapshot.observation == ObservationStatus.UNAVAILABLE
    assert snapshot.routes == []


def test_collect_routing_snapshot_not_observed_without_bird_container() -> None:
    # 没有 BIRD 服务的状态：观察器无法构造 → NOT_OBSERVED（跳过该维度）。
    fake_state = SimpleNamespace(
        node=SimpleNamespace(node_id="n1"),
        runtime=SimpleNamespace(project_name=None, services=[]),
    )
    snapshot = collect_routing_snapshot(
        fake_state, _FakeExec({}), captured_at="2024-06-01T10:00:00+00:00"  # type: ignore[arg-type]
    )
    assert snapshot.observation == ObservationStatus.NOT_OBSERVED


# ---- 过滤前(import-table) RPKI 分布 ----

_PROTOCOLS = "\n".join(
    [
        "BIRD 2.17.1 ready.",
        "Name       Proto      Table      State  Since         Info",
        "demopeer_v4 BGP        ---        up     2024-06-01    Established",
        "ibgp_pvg2  BGP        ---        up     2024-06-01    Established",
        "int_ospf   OSPF       ---        up     2024-06-01    Running",
    ]
)

# import-table（过滤前）：一条 ROA-valid + 一条无 ROA(not-found)。
_IMPORT_V4 = "\n".join(
    [
        "Table import:",
        "172.20.0.0/24        unicast [demopeer_v4 2024-06-01 10:00:00] * (100) [AS4242420000i]",
        "\tBGP.as_path: 4242420001 4242420000",
        "198.51.100.0/24      unicast [demopeer_v4 2024-06-01 10:00:00] * (100) [AS64999i]",
        "\tBGP.as_path: 4242420001 64999",
    ]
)

# 过滤后主表只剩 ROA-valid 那条。
_MASTER_PF = "\n".join(
    [
        "Table master4:",
        "172.20.0.0/24        unicast [demopeer_v4 2024-06-01 10:00:00] * (100) [AS4242420000i]",
        "\tBGP.as_path: 4242420001 4242420000",
    ]
)

_ROA_PF = "\n".join(["Table roa4:", "172.20.0.0/24-24 AS4242420000"])


def test_parse_ebgp_protocol_names_excludes_ibgp_and_non_bgp() -> None:
    assert parse_ebgp_protocol_names(_PROTOCOLS) == ["demopeer_v4"]


def test_observe_prefilter_surfaces_filtered_rpki() -> None:
    def import_table_runner(proto: str, channel: str) -> str | None:
        return _IMPORT_V4 if (proto == "demopeer_v4" and channel == "ipv4") else None

    obs = RouteTableObserver(
        command_runner=lambda: _MASTER_PF,
        roa_runner=lambda: _ROA_PF,
        protocols_runner=lambda: _PROTOCOLS,
        import_table_runner=import_table_runner,
    )
    observed = obs.observe()
    assert observed is not None
    pf = obs.observe_prefilter(observed)
    assert pf is not None
    # 过滤前收到 2 条、进主表 1 条；过滤前才看得到那条 not-found。
    assert pf.received == 2 and pf.accepted == 1
    assert pf.valid == 1 and pf.not_found == 1 and pf.invalid == 0
    assert len(pf.peers) == 1
    peer = pf.peers[0]
    assert peer.protocol == "demopeer_v4"
    assert peer.remote_asn == 4242420001
    assert peer.received == 2 and peer.accepted == 1
    assert peer.valid == 1 and peer.not_found == 1


def test_observe_prefilter_none_without_runners() -> None:
    obs = RouteTableObserver(command_runner=lambda: _MASTER_PF, roa_runner=lambda: _ROA_PF)
    assert obs.observe_prefilter(obs.observe() or []) is None


def test_aggregate_prefilter_lists_invalid_routes() -> None:
    """无效路由（有 ROA 覆盖但起源不符）被收进 invalid_routes 明细。"""

    roa = RpkiIndex.from_bird("Table roa4:\n172.20.0.0/16-24 AS4242420000")
    text = "\n".join(
        [
            "Table import:",
            "172.20.0.0/24        unicast [p 2024-06-01 10:00:00] * (100) [AS4242420000i]",
            "\tBGP.as_path: 4242420001 4242420000",
            "172.20.9.0/24        unicast [p 2024-06-01 10:00:00] * (100) [AS64999i]",
            "\tBGP.as_path: 4242420001 64999",
        ]
    )
    routes = parse_bird_routes(text)
    pf = aggregate_prefilter({"p": routes}, {"p": 1}, roa)

    assert pf.valid == 1 and pf.invalid == 1
    assert len(pf.invalid_routes) == 1
    bad = pf.invalid_routes[0]
    assert bad.prefix == "172.20.9.0/24"
    assert bad.origin_asn == 64999
    assert bad.protocol == "p"


def test_aggregate_prefilter_lists_policy_filtered_routes() -> None:
    """过滤前收到、没进主表、又非 RPKI 无效的路由进 filtered_routes（被策略拒绝）。"""

    roa = RpkiIndex.from_bird("Table roa4:\n172.20.0.0/16-24 AS4242420000")
    text = "\n".join(
        [
            "Table import:",
            # 通过过滤(进主表) —— 不应出现在 filtered_routes
            "172.20.0.0/24        unicast [p 2024-06-01 10:00:00] * (100) [AS4242420000i]",
            "\tBGP.as_path: 4242420001 4242420000",
            # RPKI 无效 —— 归 invalid_routes,不归 filtered_routes
            "172.20.9.0/24        unicast [p 2024-06-01 10:00:00] * (100) [AS64999i]",
            "\tBGP.as_path: 4242420001 64999",
            # bogon(not-found)且没进主表 —— 被策略过滤器主动拒绝
            "10.0.0.0/8           unicast [p 2024-06-01 10:00:00] * (100) [AS4242420000i]",
            "\tBGP.as_path: 4242420001 4242420000",
        ]
    )
    routes = parse_bird_routes(text)
    # 主表只接受了第一条
    accepted_keys = {("172.20.0.0/24", "p")}
    pf = aggregate_prefilter({"p": routes}, {"p": 1}, roa, accepted_keys)

    assert [r.prefix for r in pf.filtered_routes] == ["10.0.0.0/8"]
    assert pf.filtered_routes[0].protocol == "p"
    # invalid 仍只在 invalid_routes,不重复进 filtered_routes
    assert {r.prefix for r in pf.invalid_routes} == {"172.20.9.0/24"}


def test_aggregate_prefilter_no_master_skips_filtered() -> None:
    """拿不到主表归属(accepted_keys 为空)时不臆造 filtered_routes。"""

    roa = RpkiIndex.from_bird("Table roa4:\n172.20.0.0/16-24 AS4242420000")
    text = "\n".join(
        [
            "Table import:",
            "10.0.0.0/8           unicast [p 2024-06-01 10:00:00] * (100) [AS4242420000i]",
            "\tBGP.as_path: 4242420001 4242420000",
        ]
    )
    routes = parse_bird_routes(text)
    pf = aggregate_prefilter({"p": routes}, {"p": 0}, roa)
    assert pf.filtered_routes == []


def test_classify_reject_reason_matches_filter_branches() -> None:
    """被拒原因判定与 import 过滤器各 reject 分支一一对应,且优先级正确。"""

    pol = RejectPolicy(
        own_nets=[ip_network("172.20.0.0/26")],
        rejected_asns=frozenset({64666}),
    )
    # 不在 DN42 合法范围(公网) → out_of_range（最高优先）
    assert classify_reject_reason("8.8.8.0/24", [4242420000], pol) == "out_of_range"
    # 合法 dn42 范围、命中本节点自有网段 → self_net
    assert classify_reject_reason("172.20.0.0/26", [4242420000], pol) == "self_net"
    # 合法前缀、AS path > 8 → as_path_too_long
    assert (
        classify_reject_reason("172.20.0.0/21", [1, 2, 3, 4, 5, 6, 7, 8, 9], pol)
        == "as_path_too_long"
    )
    # 合法前缀、path 含拒收 ASN → blocked_asn
    assert classify_reject_reason("172.20.0.0/21", [4242420000, 64666], pol) == "blocked_asn"
    # 合法前缀、正常 path、无 self/blocked → policy 兜底
    assert classify_reject_reason("172.20.0.0/21", [4242420000], pol) == "policy"


def test_aggregate_prefilter_tags_filtered_reason() -> None:
    """filtered_routes 每条带上首要拒绝原因（这里是越界 bogon）。"""

    roa = RpkiIndex.from_bird("Table roa4:\n172.20.0.0/16-24 AS4242420000")
    text = "\n".join(
        [
            "Table import:",
            "8.8.8.0/24           unicast [p 2024-06-01 10:00:00] * (100) [AS4242420000i]",
            "\tBGP.as_path: 4242420001 4242420000",
        ]
    )
    routes = parse_bird_routes(text)
    pol = RejectPolicy(own_nets=[], rejected_asns=frozenset())
    # accepted_keys 非空(有主表)但不含该路由 ⇒ 进 filtered_routes
    pf = aggregate_prefilter({"p": routes}, {"p": 0}, roa, {("172.20.0.0/24", "p")}, pol)
    assert len(pf.filtered_routes) == 1
    assert pf.filtered_routes[0].reason == "out_of_range"
