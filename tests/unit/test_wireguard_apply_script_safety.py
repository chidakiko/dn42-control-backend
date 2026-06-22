from __future__ import annotations

"""单接口 WireGuard apply 脚本的"无弹跳"回归锁（review finding #7）。

定向收敛对**被改的隧道**重放 ``apply-<iface>.sh``。该脚本必须是就地热更新：

- ``wg syncconf``（差量同步，端点 / keepalive / peer 变更不打断既有会话）；
- ``ip addr replace`` / ``ip route replace``（幂等，不先删后加）；
- 绝不出现 ``wg setconf``（全量重置）、``wg-quick up/down``、``ip link del``、
  ``link set ... down`` 或 ``addr flush`` 等会把隧道打断的动作。

一旦有人把脚本改回"先拆后建"，本测试立即失败。
"""

import pytest

from dn42_schemas import InterfaceKind, InterfaceSpec, WireGuardPeerSpec
from dn42_schemas.testing import build_hkg1_example_state
from dn42_templates import render_wireguard_apply_script
from dn42_templates.scripts import _host_route

_FORBIDDEN_FRAGMENTS = (
    "wg setconf",
    "wg-quick up",
    "wg-quick down",
    "ip link del",
    "addr flush",
    "addr del",
    "route del",
    "down",
)


def _wireguard_interfaces():
    state = build_hkg1_example_state()
    interfaces = [i for i in state.interfaces if i.kind == InterfaceKind.WIREGUARD]
    assert interfaces, "示例 state 应包含 WireGuard 接口"
    return interfaces


def test_apply_script_uses_in_place_sync() -> None:
    for interface in _wireguard_interfaces():
        script = render_wireguard_apply_script(interface)
        assert "wg syncconf" in script, interface.name
        # 接口只在缺失时创建，存在时绝不重建。
        assert f'ip link show "${{IF}}" >/dev/null 2>&1 || ip link add' in script, interface.name


def test_apply_script_has_no_disruptive_commands() -> None:
    for interface in _wireguard_interfaces():
        script = render_wireguard_apply_script(interface)
        for fragment in _FORBIDDEN_FRAGMENTS:
            assert fragment not in script, f"{interface.name}: 脚本含破坏性片段 {fragment!r}"


def test_apply_script_addresses_and_routes_are_idempotent_replace() -> None:
    for interface in _wireguard_interfaces():
        script = render_wireguard_apply_script(interface)
        for line in script.splitlines():
            stripped = line.strip()
            if stripped.startswith(("ip addr", "ip -6 addr")):
                assert " replace " in stripped, f"{interface.name}: {stripped}"
            if stripped.startswith(("ip route", "ip -6 route")):
                assert " replace " in stripped, f"{interface.name}: {stripped}"


@pytest.mark.parametrize(
    "route,expected",
    [
        # 链路本地 /64：主机位 ::298 必须保留，绝不能被掩成 fe80::。
        ("fe80::298/64", "fe80::298/128"),
        ("fe80::29/64", "fe80::29/128"),
        # 非 /32 的 v4 peer 路由同样要保留主机位。
        ("10.0.0.2/30", "10.0.0.2/32"),
        # /32 / /128 本就是主机，保持不变。
        ("172.20.1.67/32", "172.20.1.67/32"),
        ("fd42:4242:1023:67::1/128", "fd42:4242:1023:67::1/128"),
    ],
)
def test_host_route_preserves_host_bits(route: str, expected: str) -> None:
    assert _host_route(route) == expected


def test_link_local_peer_renders_host_address_not_network() -> None:
    """link-local eBGP 建邻：``ip addr ... peer`` 必须指向对端主机 ``fe80::298``。

    回归锁住曾把 ``fe80::298/64`` 掩成 ``peer fe80::`` 的 bug——那会让内核装不出到
    对端的点对点路由，邻居不可达、BGP 卡 Idle。
    """

    interface = InterfaceSpec(
        name="as4242420298",
        kind=InterfaceKind.WIREGUARD,
        addresses=["fe80::28/64"],
        peer_routes=["fe80::298/64"],
        private_key_ref="secret://node.key",
        wireguard_peer=WireGuardPeerSpec(
            public_key="+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ="
        ),
    )
    script = render_wireguard_apply_script(interface)
    assert "fe80::298/128" in script  # 指向对端主机
    assert "peer 'fe80::/128'" not in script  # 旧 bug 的全零 peer 形态
    assert "fe80::/128" not in script
