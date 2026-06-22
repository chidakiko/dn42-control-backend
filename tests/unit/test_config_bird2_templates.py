from __future__ import annotations

"""bird2 配置模板渲染器的单元测试。

bird2 是 DN42 路由堆栈的核心。本文件锁定 “从 DesiredState 到 jinja2
上下文”这一转换，避免其中任何一个变量被重构错位：

* ``build_config_bird2_context``：验证 ASN / router_id / loopback /
  RPKI listener / region 号 / ratelimit / 节点名 / hostname / 内部拓扑路由器
  列表、及 “每个内部 router 对应一台 host” 的 ``bird_hosts`` 子表。还
  包括 OSPF 邻居接口与 cost、stub / anycast 接口名、ipset 后缀，以及
  每个 wg peer 的 BGP 嫹举（import / export / v4 / v6）。
* ``render_config_bird2_set``：返回的文件集名与
  ``CONFIG_BIRD2_TEMPLATE_NAMES`` 严格对应（去掉 ``.j2`` 后缀），
  主配置、dn42_peers、ibgp、ospf_interfaces 中出现预期的 protocol /
  neighbor / interface 定义。
* ``render_config_bird2_template``：同一名称带不带 ``.j2`` 后缀产出
  相同结果，避免上层代码处理 suffix 不一致造成错误。
"""

from dn42_common import Dn42OriginRegionCommunity
from dn42_schemas.testing import build_hkg1_example_state
from dn42_templates import (
    CONFIG_BIRD2_TEMPLATE_NAMES,
    build_config_bird2_context,
    render_config_bird2_set,
    render_config_bird2_template,
)


def test_build_config_bird2_context_maps_desired_state_to_explicit_domain_variables() -> None:
    context = build_config_bird2_context(build_hkg1_example_state())

    assert context["ownas"] == 4242420000
    assert context["ownip"] == "172.20.0.62"
    assert context["ownip6"] == "fdce:1111:2222:9500::1"
    assert context["rpki_ip"] == "10.254.42.3"
    assert context["region"] == Dn42OriginRegionCommunity.ASIA_EAST
    assert context["dn42_region"] == 52
    assert context["dn42_ratelimit"] == 15
    assert context["dn42_import_limit"] == 8500
    assert context["dn42_import_limit_action"] == "block"
    assert context["node_id"] == "edge1"
    assert context["bird_hostname"] == "edge1"
    assert context["internal_router_names"] == ["edge1", "edge2"]
    assert context["bird_hosts"]["edge2"]["ownip6"] == "fdce:1111:2222:ff01::3"
    assert context["ospf_neighbor_interfaces"] == [
        {"name": "igp-edge2", "peer_node": "edge2", "cost": 10, "iface_type": "ptp"}
    ]
    assert context["stub_interface_names"] == []
    # DNS 启用的黄金样本：dns.bind_addresses 派生出托管 dns-anycast 接口，进 direct_anycast。
    assert context["anycast_interface_names"] == ["dns-anycast"]
    assert context["ownnets4_ipset"] == "172.20.0.0/26+"
    assert context["ownnets6_ipset"] == "fdce:1111:2222::/48+"
    assert [peer["name"] for peer in context["wg_peers"]] == ["as4242420001"]
    assert context["wg_peers"][0]["name"] == "as4242420001"
    assert context["wg_peers"][0]["bgp"]["asn"] == 4242420001
    assert context["wg_peers"][0]["bgp"]["import_mode"] == "filter"
    assert context["wg_peers"][0]["bgp"]["export_mode"] == "filter"
    assert context["wg_peers"][0]["bgp"]["ipv4"] is True
    assert context["wg_peers"][0]["bgp"]["ipv6"] is True


def test_config_bird2_templates_render_as_a_complete_file_set() -> None:
    context = build_config_bird2_context(build_hkg1_example_state())
    rendered = render_config_bird2_set(context)
    paths = {file.path for file in rendered}

    assert paths == {name.removesuffix(".j2") for name in CONFIG_BIRD2_TEMPLATE_NAMES}

    bird_conf = next(file.content for file in rendered if file.path == "bird.conf")
    assert "define OWNAS = 4242420000;" in bird_conf
    assert "define DN42_REGION = 52;" in bird_conf
    assert 'hostname "edge1";' in bird_conf
    assert 'include "/etc/bird/rpki.conf";' in bird_conf
    assert 'include "/etc/bird/dn42_peers.conf";' in bird_conf

    dn42_peers_conf = next(file.content for file in rendered if file.path == "dn42_peers.conf")
    assert "protocol bgp demopeer_4242420001_ex01_v4 from dnpeers" in dn42_peers_conf
    assert "protocol bgp demopeer_4242420001_ex01_v6 from dnpeers" in dn42_peers_conf
    assert "neighbor 172.20.0.105 as 4242420001;" in dn42_peers_conf
    assert "neighbor fdce:1111:2222:dead::11 as 4242420001;" in dn42_peers_conf
    assert "AS4242420000" not in dn42_peers_conf

    ibgp_conf = next(file.content for file in rendered if file.path == "ibgp.conf")
    assert "protocol bgp ibgp_edge2" in ibgp_conf
    assert "neighbor fdce:1111:2222:ff01::3 as 4242420000;" in ibgp_conf

    ospf_interfaces_conf = next(
        file.content for file in rendered if file.path == "ospf_interfaces.conf"
    )
    assert 'interface "igp-edge2"' in ospf_interfaces_conf
    assert "cost 10;" in ospf_interfaces_conf

    # 任播服务模板的"启用"分支：DNS 启用的样本派生出 dns-anycast 接口 ⇒ direct_anycast
    # 把它纳入 direct protocol（这条前缀因此被起源/宣告）。bird.conf 也确实 include 它。
    anycast_conf = next(file.content for file in rendered if file.path == "anycast_services.conf")
    assert "protocol direct direct_anycast {" in anycast_conf
    assert '"dns-anycast"' in anycast_conf
    assert 'include "/etc/bird/anycast_services.conf";' in bird_conf


def test_anycast_services_conf_is_empty_when_dns_disabled() -> None:
    """无 track_service 接口（dns=None）⇒ direct_anycast 不生成，渲染为停用注释。"""

    data = build_hkg1_example_state().model_dump(mode="json")
    data["dns"] = None
    state = build_hkg1_example_state().__class__.model_validate(data)

    context = build_config_bird2_context(state)
    assert context["anycast_interface_names"] == []
    anycast_conf = next(
        file.content
        for file in render_config_bird2_set(context)
        if file.path == "anycast_services.conf"
    )
    assert "protocol direct direct_anycast" not in anycast_conf
    assert "未启用任播服务" in anycast_conf


def test_config_bird2_template_name_resolves_with_or_without_j2_suffix() -> None:
    context = build_config_bird2_context(build_hkg1_example_state())

    assert render_config_bird2_template("rpki.conf", context) == render_config_bird2_template(
        "rpki.conf.j2",
        context,
    )


def _state_with_collector():
    from dn42_schemas import AddressFamily, BgpSessionSpec

    state = build_hkg1_example_state()
    session = BgpSessionSpec(
        name="dn42_grc_4242422602_services",
        remote_asn=4242422602,
        neighbor="172.20.0.179",
        source_address="172.20.0.1",
        address_family=AddressFamily.MP_BGP,
        interface=None,
        policy="route_collector",
        bfd=None,
    )
    return state.model_copy(update={"bgp_sessions": [*state.bgp_sessions, session]})


def test_route_collector_session_renders_multihop_feed() -> None:
    context = build_config_bird2_context(_state_with_collector())
    # 进入 route_collectors 上下文（无接口的多跳会话）。
    assert context["route_collectors"] == [
        {
            "name": "dn42_grc_4242422602_services",
            "neighbor": "172.20.0.179",
            "asn": 4242422602,
            "source_address": "172.20.0.1",
        }
    ]
    bird_conf = next(
        file.content for file in render_config_bird2_set(context) if file.path == "bird.conf"
    )
    # 用现成 route_collector 模板实例化：neighbor + source address。
    assert "protocol bgp dn42_grc_4242422602_services from route_collector {" in bird_conf
    assert "neighbor 172.20.0.179 as 4242422602;" in bird_conf
    assert "source address 172.20.0.1;" in bird_conf
    # 无接口的收集器会话绝不进 wg peer 渲染路径（避免重复/残缺）。
    peers_conf = next(
        file.content for file in render_config_bird2_set(context) if file.path == "dn42_peers.conf"
    )
    assert "4242422602" not in peers_conf


def test_route_collector_absent_renders_no_feed_protocol() -> None:
    # 无收集器会话时，bird.conf 不出现任何 `from route_collector` 协议实例（仅保留模板定义）。
    bird_conf = next(
        file.content
        for file in render_config_bird2_set(build_config_bird2_context(build_hkg1_example_state()))
        if file.path == "bird.conf"
    )
    assert "from route_collector {" not in bird_conf
    assert "template bgp route_collector {" in bird_conf
