from __future__ import annotations

"""三节点本地 lab（``hkg1`` / ``hk2`` / ``tyo1``）加 ``ext1`` eBGP 外部 peer
的渲染脚本（``scripts/dev/render-local-three-node.py``）集成测试。

该 lab 是开发者在本地验证 “多节点 + 外部 peer” 场景的最小可运行型，
本文件锐意锁定：

* 三个内部节点互为 full-mesh iBGP：BIRD 上下文的
  ``internal_router_names`` 与 ``ospf_neighbor_interfaces`` 長度为 2，每个
  节点产出 2 个 ``wireguard/igp-*`` 与对应 apply 脚本、ibgp.conf 中
  为另外两个 peer 生成 ``protocol bgp ibgp_<peer>`` 定义。
* 三个内部节点都与 ext1 走 eBGP，各自产出
  ``wireguard/as4242420002.conf`` 与 ``protocol bgp ebgp_4242420002_v4/v6``。
* 每个节点独立输出一份 ``docker/router/Dockerfile``（**不再有**
  docker-compose.yml——容器编排由结构化 runtime 数据驱动），路由 image
  是本地 build（不含 ``DEBIAN_MIRROR`` build args，默认源为
  ``deb.debian.org``）；underlay subnet / RPKI listener 锁在 runtime 结构上。
* 每个节点发布的外口 UDP 端口 (igp + eBGP) 是互斥的。
"""

from pathlib import Path

from dn42_schemas import DesiredState, resolve_service_ipv4, resolve_service_sysctls
from dn42_runtime import render_router_dockerfile, write_rendered_files
from dn42_schemas.testing import build_local_three_node_states
from dn42_templates import build_config_bird2_context, render_desired_state


def _states_by_directory() -> dict[str, DesiredState]:
    return {directory: state for directory, state in build_local_three_node_states()}


def test_three_node_lab_renders_full_mesh_ibgp_and_igp_links() -> None:
    states = _states_by_directory()

    for directory in ("hkg1", "hk2", "tyo1"):
        state = states[directory]
        context = build_config_bird2_context(state)
        rendered = render_desired_state(state)
        paths = {file.path for file in rendered}
        ibgp = next(file.content for file in rendered if file.path == "bird/ibgp.conf")

        assert context["internal_router_names"] == ["edge1", "edge2", "edge3"]
        assert len(context["ospf_neighbor_interfaces"]) == 2
        assert sum(path.startswith("wireguard/igp-") for path in paths) == 2
        assert sum(path.startswith("scripts/wg/apply-igp-") for path in paths) == 2

        for peer in [entry["peer_node"] for entry in context["ospf_neighbor_interfaces"]]:
            assert f"protocol bgp ibgp_{peer.replace('-', '_')}" in ibgp

        dn42_peers = next(file.content for file in rendered if file.path == "bird/dn42_peers.conf")
        assert "wireguard/as4242420002.conf" in paths
        assert "scripts/wg/apply-as4242420002.sh" in paths
        assert "protocol bgp ebgp_4242420002_v4" in dn42_peers
        assert "protocol bgp ebgp_4242420002_v6" in dn42_peers

    ext1_state = states["ext1"]
    ext1_rendered = render_desired_state(ext1_state)
    ext1_paths = {file.path for file in ext1_rendered}
    extpeers = next(file.content for file in ext1_rendered if file.path == "bird/dn42_peers.conf")

    assert "wireguard/as0028-hkg1.conf" in ext1_paths
    assert "wireguard/as0028-hk2.conf" in ext1_paths
    assert "wireguard/as0028-tyo1.conf" in ext1_paths
    assert "protocol bgp ebgp_4242420000_hkg1_v4" in extpeers
    assert "protocol bgp ebgp_4242420000_hk2_v4" in extpeers
    assert "protocol bgp ebgp_4242420000_tyo1_v4" in extpeers


def _enabled_services(state: DesiredState) -> dict[str, object]:
    return {service.name: service for service in state.runtime.services if service.enabled}


def _published_ports(state: DesiredState) -> set[tuple[int, int, str]]:
    """展开节点全部 enabled 服务的 (host, container, protocol) 端口发布。"""

    published: set[tuple[int, int, str]] = set()
    for service in state.runtime.services:
        if not service.enabled:
            continue
        for port in service.ports:
            if port.host_port is None:
                continue
            container_end = port.container_port_end or port.container_port
            for offset in range(container_end - port.container_port + 1):
                published.add(
                    (port.host_port + offset, port.container_port + offset, port.protocol)
                )
    return published


def test_three_node_lab_runtime_has_expected_services(tmp_path: Path) -> None:
    states = _states_by_directory()
    for directory, state in states.items():
        write_rendered_files(render_desired_state(state), tmp_path / directory)

    for directory, state in states.items():
        # 容器编排与镜像构建都不再渲染文件。
        assert not (tmp_path / directory / "docker-compose.yml").exists()
        assert not (tmp_path / directory / "docker" / "router" / "Dockerfile").exists()
        dockerfile = render_router_dockerfile(state.runtime.router_dockerfile)
        assert 'FROM debian:13-slim AS debian-base' in dockerfile
        assert 'deb.debian.org/debian-security' in dockerfile

        services = _enabled_services(state)
        for required in ("dn42-router-netns", "dn42-wg-gateway", "dn42-bird-router", "dn42-rpki-cache"):
            assert required in services

        router = services["dn42-router-netns"]
        assert router.build is not None
        assert router.build.target == "netns"
        assert "DEBIAN_MIRROR" not in router.build.args
        assert resolve_service_sysctls(router)["net.ipv6.conf.all.forwarding"] == "1"

        rpki = services["dn42-rpki-cache"]
        assert resolve_service_ipv4(state.runtime, rpki) == state.runtime.rpki.listen_host

    assert {
        (32001, 30001, "udp"),
        (32003, 30002, "udp"),
        (32129, 31029, "udp"),
    } <= _published_ports(states["hkg1"])

    assert {
        (32002, 30001, "udp"),
        (32005, 30003, "udp"),
        (32130, 31030, "udp"),
    } <= _published_ports(states["hk2"])

    assert {
        (32004, 30002, "udp"),
        (32006, 30003, "udp"),
        (32131, 31031, "udp"),
    } <= _published_ports(states["tyo1"])

    assert {
        (32229, 31029, "udp"),
        (32230, 31030, "udp"),
        (32231, 31031, "udp"),
    } <= _published_ports(states["ext1"])


def test_three_node_lab_node_runtime_settings_propagate() -> None:
    states = _states_by_directory()

    hkg1 = states["hkg1"]
    assert hkg1.runtime.router_dockerfile.base_image == "debian:13-slim"
    assert hkg1.runtime.router_dockerfile.debian_mirror == "deb.debian.org"
    assert hkg1.runtime.underlay.subnet == "10.254.45.0/24"
    assert hkg1.runtime.rpki.listen_host == "10.254.45.10"

    ext1 = states["ext1"]
    assert ext1.node.asn == 4242420002
