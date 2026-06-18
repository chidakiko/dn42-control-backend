from __future__ import annotations

""":class:`NodeSpec` 在 DN42 资产范围上的专项校验测试。

``NodeSpec`` 携带一台路由器的 DN42 资产集（ASN、router id、IPv4 / IPv6
段）。本文件锐意锁定以下拒绝路径，避免后续重构让“不属于 DN42
的地址”被接受并进入 BIRD 配置：

* DN42 空间内的 IPv4 / IPv6 prefix 可被接受；任何越界 prefix、或
  者使用过于宽松的 supernet、以及 DN42 closed / reserved 保留段 会
  被拒绝，错误信息与 ``dn42_common.validate_dn42_*_network`` 一致。
* ``172.20.0.0/24`` 这类 anycast 段默认被接受（供 anycast service 使用）。
"""

from typing import Any

import pytest
from pydantic import ValidationError

from dn42_common import Dn42OriginRegionCommunity
from dn42_schemas import NodeSpec


def _make_node(**overrides) -> NodeSpec:
    base: dict[str, Any] = dict(
        node_id="hkg1",
        site="hkg",
        region=Dn42OriginRegionCommunity.ASIA_EAST,
        asn=4_242_420_001,
        router_id="172.20.0.1",
        ipv4_prefixes=["172.20.0.0/26"],
        ipv6_prefixes=["fdce:1111:2222::/48"],
    )
    base.update(overrides)
    return NodeSpec(**base)


class TestNodeSpecDn42Prefixes:
    def test_accepts_dn42_prefixes(self) -> None:
        node = _make_node()
        assert node.ipv4_prefixes == ["172.20.0.0/26"]
        assert node.ipv6_prefixes == ["fdce:1111:2222::/48"]

    def test_rejects_ipv4_outside_dn42(self) -> None:
        with pytest.raises(ValidationError, match="outside dn42 IPv4 space"):
            _make_node(ipv4_prefixes=["10.0.0.0/24"])

    def test_rejects_ipv6_outside_dn42(self) -> None:
        with pytest.raises(ValidationError, match="outside dn42 IPv6 space"):
            _make_node(ipv6_prefixes=["2001:db8::/32"])

    def test_rejects_closed_ipv4_range(self) -> None:
        with pytest.raises(ValidationError, match="closed allocation"):
            _make_node(ipv4_prefixes=["172.23.16.0/24"])

    def test_rejects_reserved_ipv4_range(self) -> None:
        with pytest.raises(ValidationError, match="reserved-for-future"):
            _make_node(ipv4_prefixes=["172.21.0.0/24"])

    def test_rejects_loose_supernet(self) -> None:
        with pytest.raises(ValidationError, match="outside dn42 IPv4 space"):
            _make_node(ipv4_prefixes=["172.16.0.0/12"])

    def test_anycast_ipv4_allowed(self) -> None:
        node = _make_node(
            ipv4_prefixes=["172.20.0.0/24"],
            router_id="172.20.0.1",
        )
        assert node.ipv4_prefixes == ["172.20.0.0/24"]
