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


def render_loopback_script(state: DesiredState, env: Environment | None = None) -> str:
    """渲染 loopback 地址配置脚本。"""

    active_env = env or create_config_scripts_environment()
    loopback = next((item for item in state.interfaces if item.name == "dn42-lo"), None)
    return active_env.get_template("wg/apply-loopback.sh.j2").render(
        address_commands=_loopback_address_commands(loopback),
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
    """渲染按顺序应用全部 WireGuard 接口脚本的聚合入口。"""

    active_env = env or create_config_scripts_environment()
    return active_env.get_template("wg/apply-all-wg.sh.j2").render(
        interface_scripts=[
            f"/opt/dn42/scripts/wg/apply-{interface.name}.sh"
            for interface in sorted(state.interfaces, key=lambda item: item.name)
            if interface.kind == InterfaceKind.WIREGUARD
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
            commands.append(f'{command} {shell_quote(address)} peer {shell_quote(peer)} dev "${{IF}}"')
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
    network = ip_network(route, strict=False)
    prefix = 32 if network.version == 4 else 128
    return f"{network.network_address}/{prefix}"


def _peer_route_for_address(address: str, peer_routes: list[str]) -> str | None:
    parsed_address = ip_interface(address)
    same_family_routes = [
        route for route in peer_routes if ip_network(route, strict=False).version == parsed_address.version
    ]
    if parsed_address.ip.is_link_local:
        # 链路本地地址（fe80::/10）只能与同为链路本地的 peer 路由配对（真正的
        # 点对点 link-local 链路），绝不能回退到 ULA/global 路由：否则 fe80::/64
        # 会退化成点对点，内核不再安装 fe80::/64 的 on-link 前缀，对端的
        # 链路本地 BGP 邻居将不可达，会话会一直卡在 Idle。
        link_local_routes = [
            route for route in same_family_routes if ip_network(route, strict=False).network_address.is_link_local
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
    "render_loopback_script",
    "render_wireguard_apply_script",
    "render_wireguard_start_script",
]
