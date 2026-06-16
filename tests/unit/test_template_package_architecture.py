from __future__ import annotations

"""非 bird 模板家族的架构不变量测试。

dn42_templates 采取家族（family）为边界的布局：每个家族拥有独立的
模板目录、互不依赖、都能独立产出可用配置。本文件锐意锁定：

* docker / wireguard / scripts / coredns 四个家族的期望的
  ``*.j2`` 模板顶层存在（被包资源获取函数返回的路径能被访问到）。
* 渲染器在有 hkg1 sample state 时产出特征性字段 (router Dockerfile /
  WireGuard PublicKey / wg-apply 脚本路径 / Corefile zone)。
* WireGuard apply 脚本针对多地址场景会对齐生成 ``ip -6 addr replace
  ... peer ...`` 列，包括 link-local fe80:: 地址。
* looking-glass 双 sidecar（proxy + frontend）以结构化 runtime 服务注入，
  带 dn42 专用环境变量、发布端口、共享 bird socket 的 volume——容器编排
  不再渲染任何文件，这些不变量直接锁在 `state.runtime` 上。
"""

from pathlib import Path

from dn42_schemas import InterfaceKind, InterfaceSpec, ServiceRole, WireGuardPeerSpec
from dn42_schemas.testing import build_hkg1_example_state
from dn42_runtime import config_docker_template_dir, render_router_dockerfile
from dn42_templates import (
    config_coredns_template_dir,
    config_scripts_template_dir,
    config_wireguard_template_dir,
    render_apply_all_wg_script,
    render_corefile,
    render_wireguard_apply_script,
    render_wireguard,
)


def test_non_bird_config_families_have_packaged_template_directories() -> None:
    expected = {
        Path(config_docker_template_dir()) / "router" / "Dockerfile.j2",
        Path(config_wireguard_template_dir()) / "interface.conf.j2",
        Path(config_scripts_template_dir()) / "wg" / "apply-all-wg.sh.j2",
        Path(config_scripts_template_dir()) / "bird" / "start-bird-router.sh.j2",
        Path(config_coredns_template_dir()) / "Corefile.j2",
    }

    assert all(path.exists() for path in expected)


def test_non_bird_renderers_use_independent_template_families() -> None:
    state = build_hkg1_example_state()
    wireguard = next(interface for interface in state.interfaces if interface.name == "as4242420001")

    dockerfile = render_router_dockerfile(state.runtime.router_dockerfile)
    wg_config = render_wireguard(wireguard)
    wg_script = render_apply_all_wg_script(state)
    corefile = render_corefile(state)

    assert "FROM debian:13-slim" in dockerfile
    assert "PublicKey = +aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=" in wg_config
    assert "/opt/dn42/scripts/wg/apply-as4242420001.sh" in wg_script
    assert "example.dn42:53" in corefile


def test_wireguard_apply_script_matches_peer_route_per_address() -> None:
    interface = InterfaceSpec(
        name="igp-edge2",
        kind=InterfaceKind.WIREGUARD,
        private_key_ref="example-private-key",
        addresses=[
            "198.18.10.0/31",
            "fdce:1111:2222:ff10::0/127",
            "fe80::202:62/64",
        ],
        peer_routes=[
            "198.18.10.1/32",
            "fdce:1111:2222:ff10::1/128",
            "fe80::202:63/128",
        ],
        wireguard_peer=WireGuardPeerSpec(
            public_key="+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ="
        ),
    )

    script = render_wireguard_apply_script(interface)

    assert "ip -6 addr replace 'fdce:1111:2222:ff10::0/127' peer 'fdce:1111:2222:ff10::1/128'" in script
    assert "ip -6 addr replace 'fe80::202:62/64' peer 'fe80::202:63/128'" in script


def test_wireguard_apply_script_keeps_link_local_plain_when_peer_route_is_ula() -> None:
    """链路本地地址不得因 ULA 点对点 peer 路由而退化成点对点。

    这是一个真实回归：MP-BGP-over-link-local 的外部 peer 接口同时带有
    ``fe80::2020/64`` 与一条 ULA 点对点（``fdc1::1 peer <对端 ULA>``）。早期
    ``_peer_route_for_address`` 的同族回退会把 ULA peer 错误地套到 fe80 地址上，
    渲染出 ``fe80::2020/64 peer <ULA>``，导致内核不再安装 fe80::/64 on-link 前缀，
    对端链路本地 BGP 邻居不可达，会话永远停在 Idle。
    """

    interface = InterfaceSpec(
        name="as4242421510",
        kind=InterfaceKind.WIREGUARD,
        private_key_ref="example-private-key",
        addresses=[
            "172.20.0.62/32",
            "fdce:1111:2222:9500::1/128",
            "fe80::2020/64",
        ],
        peer_routes=[
            "172.23.70.38/32",
            "fd6a:93d4:3358::6/128",
        ],
        wireguard_peer=WireGuardPeerSpec(
            public_key="+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ="
        ),
    )

    script = render_wireguard_apply_script(interface)

    # 链路本地保持普通 /64，不带 peer
    assert "ip -6 addr replace 'fe80::2020/64' dev" in script
    assert "ip -6 addr replace 'fe80::2020/64' peer" not in script
    # ULA 点对点仍正确套用对端 ULA
    assert "ip -6 addr replace 'fdce:1111:2222:9500::1/128' peer 'fd6a:93d4:3358::6/128'" in script


def test_runtime_carries_lookglass_sidecars_with_environment_and_ports() -> None:
    state = build_hkg1_example_state()
    services = {service.role: service for service in state.runtime.services if service.enabled}

    proxy = services[ServiceRole.LOOKING_GLASS_PROXY]
    frontend = services[ServiceRole.LOOKING_GLASS_FRONTEND]

    assert proxy.environment["BIRD_SOCKET"] == "/run/bird/bird.ctl"
    assert frontend.environment["BIRDLG_TITLE_BRAND"] == "DN42 looking glass"
    assert frontend.environment["BIRDLG_SERVERS"] == "dn42-router-netns"
    assert any(
        port.host_port == 5000 and port.container_port == 5000 for port in frontend.ports
    )
    assert any(
        mount.source.endswith("runtime/bird-run") and mount.target == "/run/bird"
        for mount in proxy.volumes
    )
