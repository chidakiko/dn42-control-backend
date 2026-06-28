from __future__ import annotations

"""节点脚本模板的上下文构造与渲染入口。"""

from ipaddress import ip_address, ip_interface, ip_network
from pathlib import Path
from typing import Any

from dn42_common import create_environment, shell_quote
from dn42_schemas import DesiredState, InterfaceKind, InterfaceSpec
from jinja2 import Environment

from .paths import config_scripts_template_dir


def create_config_scripts_environment(template_dir: str | Path | None = None) -> Environment:
    """创建用于渲染节点脚本模板的 Jinja2 环境。"""

    return create_environment(template_dir or config_scripts_template_dir())


def render_dummy_apply_script(interface: InterfaceSpec, env: Environment | None = None) -> str:
    """渲染单个 dummy 接口（dn42-lo、dns-anycast 等）的创建 + 地址应用脚本。

    所有 dummy 接口共用一套模板：``ip link add ... type dummy`` 幂等创建，再 ``ip addr
    replace`` 逐地址同步。dn42-lo 承载节点身份地址；dns-anycast 由 ``dns.bind_addresses``
    派生、承载任播服务地址（见 dn42_schemas.desired_state._normalize_dns_anycast）。
    """

    active_env = env or create_config_scripts_environment()
    return active_env.get_template("wg/apply-dummy.sh.j2").render(
        interface_name=interface.name,
        address_commands=_loopback_address_commands(interface),
    )


def render_wireguard_apply_script(
    interface: InterfaceSpec,
    env: Environment | None = None,
) -> str:
    """渲染单个 WireGuard 接口的地址与路由应用脚本。"""

    active_env = env or create_config_scripts_environment()
    return active_env.get_template("wg/apply-interface.sh.j2").render(
        interface_name=interface.name,
        mtu=interface.mtu or 1420,
        address_commands=_wireguard_address_commands(interface),
        route_commands=_wireguard_route_commands(interface),
    )


def render_apply_all_wg_script(state: DesiredState, env: Environment | None = None) -> str:
    """渲染按顺序应用全部受管接口脚本的聚合入口（容器自启时调用）。

    收录**所有 dummy 接口**（dn42-lo 身份地址、dns-anycast 任播地址等）与所有
    WireGuard 接口——dummy 在前，先补身份/任播地址再起隧道。这样 wg-gateway 容器
    每次启动都把 netns 的 L3 状态一次性补齐、自给自足，任何重启都能自愈，**不依赖
    agent 的 convergence**：外部重启（如 ``systemctl restart docker``）不触发
    convergence，曾导致只有 dn42-lo/WG 被自启脚本恢复、dns-anycast 漏建，进而 bird
    的启动脚本死等 dns-anycast 接口、永不启动。
    """

    active_env = env or create_config_scripts_environment()
    managed = [
        interface
        for interface in state.interfaces
        if interface.kind in {InterfaceKind.DUMMY, InterfaceKind.WIREGUARD}
    ]
    ordered = sorted(
        managed, key=lambda item: (item.kind != InterfaceKind.DUMMY, item.name)
    )
    return active_env.get_template("wg/apply-all-wg.sh.j2").render(
        interface_scripts=[
            f"/opt/dn42/scripts/wg/apply-{interface.name}.sh" for interface in ordered
        ]
    )


def render_wireguard_start_script(env: Environment | None = None) -> str:
    """渲染 WireGuard gateway 容器的启动脚本。"""

    active_env = env or create_config_scripts_environment()
    return active_env.get_template("wg/start-wg-gateway.sh.j2").render()


def render_bird_apply_script(env: Environment | None = None) -> str:
    """渲染 BIRD 配置应用脚本。"""

    active_env = env or create_config_scripts_environment()
    return active_env.get_template("bird/apply-bird.sh.j2").render()


def render_bird_start_script(state: DesiredState, env: Environment | None = None) -> str:
    """渲染 BIRD 路由器启动脚本。

    这里会把期望出现的 dummy / WireGuard 接口名注入模板，
    让启动脚本可以在 BIRD 启动前检查接口是否已经准备就绪。
    """

    active_env = env or create_config_scripts_environment()
    return active_env.get_template("bird/start-bird-router.sh.j2").render(
        expected_interfaces=[
            interface.name
            for interface in sorted(state.interfaces, key=lambda item: item.name)
            if interface.kind in {InterfaceKind.DUMMY, InterfaceKind.WIREGUARD}
        ]
    )


def build_config_scripts_context(state: DesiredState) -> dict[str, Any]:
    """构造脚本模板常用的聚合上下文。"""

    return {
        "expected_bird_interfaces": [
            interface.name
            for interface in sorted(state.interfaces, key=lambda item: item.name)
            if interface.kind in {InterfaceKind.DUMMY, InterfaceKind.WIREGUARD}
        ],
        "wireguard_interfaces": [
            interface.name
            for interface in sorted(state.interfaces, key=lambda item: item.name)
            if interface.kind == InterfaceKind.WIREGUARD
        ],
    }


def _loopback_address_commands(interface: InterfaceSpec | None) -> list[str]:
    if interface is None:
        return []
    commands: list[str] = []
    for address in sorted(interface.addresses, key=_ip_sort_key):
        command = "ip -6 addr replace" if _ip_version(address) == 6 else "ip addr replace"
        commands.append(f'{command} {shell_quote(address)} dev "${{IF}}"')
    return commands


def _wireguard_address_commands(interface: InterfaceSpec) -> list[str]:
    commands: list[str] = []
    for address in sorted(interface.addresses, key=_ip_sort_key):
        version = _ip_version(address)
        command = "ip -6 addr replace" if version == 6 else "ip addr replace"
        peer_route = _peer_route_for_address(address, interface.peer_routes)
        if peer_route:
            peer = _host_route(peer_route)
            commands.append(
                f'{command} {shell_quote(address)} peer {shell_quote(peer)} dev "${{IF}}"'
            )
        else:
            commands.append(f'{command} {shell_quote(address)} dev "${{IF}}"')
    return commands


def _wireguard_route_commands(interface: InterfaceSpec) -> list[str]:
    commands: list[str] = []
    for route in sorted(interface.peer_routes, key=_ip_sort_key):
        command = "ip -6 route replace" if _ip_version(route) == 6 else "ip route replace"
        commands.append(f'{command} {shell_quote(route)} dev "${{IF}}"')
    return commands


def _ip_version(value: str) -> int:
    parsed = ip_interface(value) if "/" in value else ip_address(value)
    return parsed.version


def _ip_sort_key(value: str) -> tuple[int, int, int]:
    if "/" in value:
        interface = ip_interface(value)
        address = interface.ip
        prefix = interface.network.prefixlen
    else:
        network = ip_network(value, strict=False)
        address = network.network_address
        prefix = network.prefixlen
    return address.version, int(address), prefix


def _host_route(route: str) -> str:
    """把 peer 路由收敛成 ``ip addr ... peer`` 所需的**主机**地址（/32 或 /128）。

    取 **主机地址** 而非网络地址：``ip_network(route).network_address`` 会掩掉主机位，
    把 ``fe80::298/64`` 变成 ``fe80::``——peer 指向全零地址，内核装不出到对端
    ``fe80::298`` 的点对点路由，link-local eBGP 邻居不可达、会话卡 Idle。只有 ``/32``
    （v4）/ ``/128``（v6）的 peer 路由侥幸不受掩码影响，其余（如 link-local 的 /64）
    必须保留主机位。
    """

    host = ip_interface(route).ip
    prefix = 32 if host.version == 4 else 128
    return f"{host}/{prefix}"


def _peer_route_for_address(address: str, peer_routes: list[str]) -> str | None:
    parsed_address = ip_interface(address)
    same_family_routes = [
        route
        for route in peer_routes
        if ip_network(route, strict=False).version == parsed_address.version
    ]
    if parsed_address.ip.is_link_local:
        # 链路本地地址（fe80::/10）只能与同为链路本地的 peer 路由配对（真正的
        # 点对点 link-local 链路），绝不能回退到 ULA/global 路由：否则 fe80::/64
        # 会退化成点对点，内核不再安装 fe80::/64 的 on-link 前缀，对端的
        # 链路本地 BGP 邻居将不可达，会话会一直卡在 Idle。
        link_local_routes = [
            route
            for route in same_family_routes
            if ip_network(route, strict=False).network_address.is_link_local
        ]
        return link_local_routes[0] if link_local_routes else None
    for route in same_family_routes:
        network = ip_network(route, strict=False)
        if network.network_address in parsed_address.network:
            return route
    return same_family_routes[0] if same_family_routes else None


__all__ = [
    "build_config_scripts_context",
    "create_config_scripts_environment",
    "render_apply_all_wg_script",
    "render_bird_apply_script",
    "render_bird_start_script",
    "render_dummy_apply_script",
    "render_wireguard_apply_script",
    "render_wireguard_start_script",
]
