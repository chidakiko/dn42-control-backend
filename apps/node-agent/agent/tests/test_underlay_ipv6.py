from __future__ import annotations

"""underlay 网络的 IPv6（NAT66 出口）支持：match 逻辑 + create 时启用 IPv6。"""

from agent.apply.docker_api import _network_matches, apply_underlay_network_plan

_V4 = {"subnet": "10.254.42.0/24", "gateway": "10.254.42.1"}
_V6 = {"ipv6_subnet": "fd00:dead:42::/64", "ipv6_gateway": "fd00:dead:42::1"}


def _net(configs: list[dict]):
    return type("N", (), {"attrs": {"IPAM": {"Config": configs}}})()


def test_match_ipv4_only_when_no_ipv6_wanted() -> None:
    net = _net([{"Subnet": "10.254.42.0/24", "Gateway": "10.254.42.1"}])
    assert _network_matches(net, **_V4) is True


def test_mismatch_when_ipv6_wanted_but_absent() -> None:
    net = _net([{"Subnet": "10.254.42.0/24", "Gateway": "10.254.42.1"}])
    assert _network_matches(net, **_V4, **_V6) is False


def test_match_when_dual_stack_present() -> None:
    net = _net(
        [
            {"Subnet": "10.254.42.0/24", "Gateway": "10.254.42.1"},
            {"Subnet": "fd00:dead:42::/64", "Gateway": "fd00:dead:42::1"},
        ]
    )
    assert _network_matches(net, **_V4, **_V6) is True


class _FakeNetworks:
    def __init__(self) -> None:
        self.created: dict[str, object] = {}

    def list(self, names: list[str]) -> list:
        return []

    def create(self, name: str, **kwargs: object) -> None:
        self.created = {"name": name, **kwargs}


class _FakeClient:
    def __init__(self) -> None:
        self.networks = _FakeNetworks()


def test_create_enables_ipv6_when_subnet_set() -> None:
    client = _FakeClient()
    res = apply_underlay_network_plan(
        client, action="create", network_name="net", **_V4, **_V6
    )
    assert res["succeeded"] is True
    assert client.networks.created.get("enable_ipv6") is True


def test_create_stays_ipv4_only_without_ipv6_subnet() -> None:
    client = _FakeClient()
    apply_underlay_network_plan(client, action="create", network_name="net", **_V4)
    assert "enable_ipv6" not in client.networks.created
