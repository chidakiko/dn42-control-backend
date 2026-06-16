from __future__ import annotations

""":class:`DesiredState` 顶层聚合体的跨字段不变量测试。

DesiredState 是控制面 -> agent 的唯一权威交换体，本文件集中验证
那些必须在实例化阶段就被拒绝的 “您望状态不一致”：

* HKG1 的 golden sample 本身可加载，所有必需服务 role
  （``router-netns`` / ``wg-gateway`` / ``bird-router`` / lookglass 对”）齐备。
* 缺少任一必需 service role 、使用未实现的 ``adapter``、BGP session
  引用不存在的接口、``network_mode`` 指向不存在的 service 都必须拒绝。
* runtime service 的 ``ipv4_address`` 必须落在下载 underlay subnet 中，
  使用 ``network_mode`` 的 service 不允许设置独立的 IP 或发布端口；
  核心 service 必须携带互联的 volume target（例如 wg-gateway 必须载
  ``/etc/wireguard``）。
* 接口名受 Linux IFNAMSIZ=15 限制、bird ``static_routes4/6`` 拒绝不合法
  prefix、``internal_topology`` 必须包含本机；loopback IPv4 必须在节点拥
  有的 prefix 中，BIRD ``region`` 必须是受支持枚举。
* 同一 WireGuard 接口不允许出现多个不同 remote ASN；large community
  拒绝 ASN=0；lookglass 为高阶抽象，不允许手写 LG service 同时使用。
"""

from dn42_common import Dn42OriginRegionCommunity
import pytest
from pydantic import ValidationError

from dn42_schemas import ServiceRole, render_port_publish
from dn42_schemas.testing import build_hkg1_example_state


def test_hkg1_example_state_is_valid() -> None:
    state = build_hkg1_example_state()

    assert state.node.node_id == "edge1"
    assert state.node.region == Dn42OriginRegionCommunity.ASIA_EAST
    assert state.generation == 1
    assert state.bird.internal_topology is not None
    assert state.bird.internal_topology.routers == ["edge1", "edge2"]
    assert state.templates.docker == "config-docker/v1"
    assert state.lookglass is not None
    assert {service.role for service in state.runtime.services}.issuperset(
        {
            ServiceRole.ROUTER_NETNS,
            ServiceRole.WG_GATEWAY,
            ServiceRole.BIRD_ROUTER,
            ServiceRole.LOOKING_GLASS_PROXY,
            ServiceRole.LOOKING_GLASS_FRONTEND,
        }
    )
    bird_router = next(service for service in state.runtime.services if service.role == ServiceRole.BIRD_ROUTER)
    assert any(mount.target == "/run/bird" and mount.readonly is False for mount in bird_router.volumes)
    assert {interface.name for interface in state.interfaces} >= {
        "dn42-lo",
        "as4242420001",
        "igp-edge2",
    }


def test_missing_required_runtime_service_is_rejected() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["services"] = [
        service
        for service in data["runtime"]["services"]
        if service["role"] != ServiceRole.WG_GATEWAY.value
    ]

    with pytest.raises(ValidationError, match="missing required runtime service roles"):
        state.__class__.model_validate(data)


def test_runtime_rejects_removed_adapter_field() -> None:
    """runtime.adapter 已随去 compose 化删除；残留输入必须显式报错而非静默忽略。"""

    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["adapter"] = "docker-compose"

    with pytest.raises(ValidationError, match="adapter"):
        state.__class__.model_validate(data)


def test_bgp_session_must_reference_existing_interface() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["bgp_sessions"][0]["interface"] = "missing0"

    with pytest.raises(ValidationError, match="missing interfaces"):
        state.__class__.model_validate(data)


def test_runtime_network_mode_must_reference_existing_service() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["services"][1]["network_mode"] = "service:missing-netns"

    with pytest.raises(ValidationError, match="unknown network_mode services"):
        state.__class__.model_validate(data)


def test_runtime_service_ipv4_address_must_belong_to_underlay_subnet() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    router_netns = next(service for service in data["runtime"]["services"] if service["name"] == "dn42-router-netns")
    router_netns["ipv4_address"] = "10.0.0.2"

    with pytest.raises(ValidationError, match="must belong to underlay subnet"):
        state.__class__.model_validate(data)


def test_runtime_service_must_not_set_ipv4_address_with_network_mode() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    bird_router = next(service for service in data["runtime"]["services"] if service["name"] == "dn42-bird-router")
    bird_router["ipv4_address"] = "10.254.42.44"

    with pytest.raises(ValidationError, match="must not set ipv4_address when using network_mode"):
        state.__class__.model_validate(data)


def test_runtime_service_must_not_publish_ports_with_network_mode() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    bird_router = next(service for service in data["runtime"]["services"] if service["name"] == "dn42-bird-router")
    bird_router["ports"] = ["18080:8080"]

    with pytest.raises(ValidationError, match="must not publish ports when using network_mode"):
        state.__class__.model_validate(data)


def test_runtime_core_service_requires_expected_volume_targets() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    wg_gateway = next(service for service in data["runtime"]["services"] if service["name"] == "dn42-wg-gateway")
    wg_gateway["volumes"] = [mount for mount in wg_gateway["volumes"] if mount["target"] != "/etc/wireguard"]

    with pytest.raises(ValidationError, match="requires volume target /etc/wireguard"):
        state.__class__.model_validate(data)


def test_interface_name_must_fit_linux_limit() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["interfaces"][0]["name"] = "interface-name-too-long"

    with pytest.raises(ValidationError, match="15 characters or fewer"):
        state.__class__.model_validate(data)


def test_bird_template_config_validates_static_routes() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["bird"]["static_routes4"] = ['172.20.0.62/32 via "dn42-lo"']
    data["bird"]["static_routes6"] = ['fdce:1111:2222:9500::1/128 via "dn42-lo"']

    validated = state.__class__.model_validate(data)

    assert validated.bird.static_routes4 == ['172.20.0.62/32 via "dn42-lo"']


def test_bird_template_config_rejects_invalid_static_routes() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["bird"]["static_routes4"] = ["not-a-prefix"]

    with pytest.raises(ValidationError):
        state.__class__.model_validate(data)


def test_internal_topology_must_include_current_node() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["bird"]["internal_topology"]["routers"] = ["edge2"]

    with pytest.raises(ValidationError, match="include the current node"):
        state.__class__.model_validate(data)


def test_node_loopback_must_belong_to_owned_prefix() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["node"]["loopback_ipv4"] = "172.20.0.64"
    data["node"]["router_id"] = "172.20.0.64"

    with pytest.raises(ValidationError, match="loopback_ipv4"):
        state.__class__.model_validate(data)


def test_bird_region_must_be_supported() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["bird"]["region"] = 999

    with pytest.raises(ValidationError):
        state.__class__.model_validate(data)


def test_wireguard_interface_must_not_mix_remote_asns() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["bgp_sessions"].append(
        {
            "name": "as4242429999_v6",
            "remote_asn": 4242429999,
            "neighbor": "fd42::1",
            "source_address": "fd42::2",
            "address_family": "ipv6",
            "interface": "as4242420001",
        }
    )

    with pytest.raises(ValidationError, match="multiple remote ASNs"):
        state.__class__.model_validate(data)


def test_large_community_rejects_invalid_blocked_asn() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["bird"]["large_communities"]["rejected_asns"] = [0]

    with pytest.raises(ValidationError, match="rejected_asns"):
        state.__class__.model_validate(data)


def test_lookglass_rejects_manual_runtime_role_overlap() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["services"] = [
        service
        for service in data["runtime"]["services"]
        if service["role"] not in {
            ServiceRole.LOOKING_GLASS_PROXY.value,
            ServiceRole.LOOKING_GLASS_FRONTEND.value,
        }
    ]
    data["runtime"]["services"].append(
        {
            "name": "manual-lg-proxy",
            "role": ServiceRole.LOOKING_GLASS_PROXY.value,
            "image": "xddxdd/bird-lgproxy-go:latest",
            "network_mode": "service:dn42-router-netns",
        }
    )

    with pytest.raises(ValidationError, match="must not define lookglass roles directly"):
        state.__class__.model_validate(data)


def test_wireguard_port_range_is_published_on_router_netns() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    router_netns = next(service for service in data["runtime"]["services"] if service["name"] == "dn42-router-netns")
    router_netns["ports"] = []
    data["runtime"]["wireguard_port_range"] = {"start": 31000, "end": 31010, "host_start": 32000}
    data["interfaces"][1]["listen_port"] = 31001
    data["interfaces"][2]["listen_port"] = 31002

    validated = state.__class__.model_validate(data)
    router_netns = next(
        service for service in validated.runtime.services if service.role == ServiceRole.ROUTER_NETNS
    )

    assert [render_port_publish(port) for port in router_netns.ports] == [
        "32000-32010:31000-31010/udp"
    ]


def test_wireguard_listen_port_must_be_inside_node_port_range() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["wireguard_port_range"] = {"start": 31000, "end": 31010}
    data["interfaces"][1]["listen_port"] = 31011

    with pytest.raises(ValidationError, match="wireguard_port_range"):
        state.__class__.model_validate(data)


def test_wireguard_listen_ports_must_be_unique_per_node() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["wireguard_port_range"] = {"start": 31000, "end": 31010}
    data["interfaces"][1]["listen_port"] = 31001
    data["interfaces"][2]["listen_port"] = 31001

    with pytest.raises(ValidationError, match="listen_port must be unique"):
        state.__class__.model_validate(data)
