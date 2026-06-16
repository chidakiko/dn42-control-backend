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
    assert context["anycast_interface_names"] == []
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


def test_config_bird2_template_name_resolves_with_or_without_j2_suffix() -> None:
    context = build_config_bird2_context(build_hkg1_example_state())

    assert render_config_bird2_template("rpki.conf", context) == render_config_bird2_template(
        "rpki.conf.j2",
        context,
    )
