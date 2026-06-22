from __future__ import annotations

"""IPv6 link-local 地址（fe80::/10）识别与校验函数的单元测试。

link-local 地址是 BIRD 上 iBGP / OSPFv3 在 WireGuard 上跑的常见选择，
为避免错误地将 global / ULA / IPv4 地址当作 link-local 接受，该套
用例覆盖：

* ``is_ipv6_link_local``：边界接受集（fe80::1、fe80::1%eth0、
  区间上界 febf::1）与拒绝集（site-local fec0::/10、loopback、
  global 2001:db8::、ULA fd00::、IPv4、空串、非字符串输入）。
* ``validate_ipv6_link_local_address``：类似语义但抛 ``ValueError``；
  ``require_zone=True`` 时必须携带 ``%zone``，且拒绝空 zone
  （``fe80::1%`` 这种静默错误路径）。
"""

import pytest

from dn42_common import (
    is_ipv6_link_local,
    validate_ipv6_link_local_address,
)


@pytest.mark.parametrize(
    "value",
    [
        "fe80::1",
        "fe80::1%eth0",
        "fe80::abcd:1234",
        "febf::1",  # 上界 fe80::/10
    ],
)
def test_is_ipv6_link_local_accepts(value: str) -> None:
    assert is_ipv6_link_local(value)


@pytest.mark.parametrize(
    "value",
    [
        "fec0::1",  # site-local（已废弃，不是 link-local）
        "::1",  # loopback
        "2001:db8::1",  # global
        "fd00::1",  # ULA
        "192.168.1.1",  # IPv4
        "",
        "not-an-address",
    ],
)
def test_is_ipv6_link_local_rejects(value: str) -> None:
    assert not is_ipv6_link_local(value)


def test_is_ipv6_link_local_non_string() -> None:
    assert not is_ipv6_link_local(None)  # type: ignore[arg-type]
    assert not is_ipv6_link_local(123)  # type: ignore[arg-type]


def test_validate_link_local_returns_value() -> None:
    assert validate_ipv6_link_local_address("fe80::1") == "fe80::1"
    assert validate_ipv6_link_local_address("fe80::1%eth0") == "fe80::1%eth0"


def test_validate_link_local_rejects_global() -> None:
    with pytest.raises(ValueError, match="link-local"):
        validate_ipv6_link_local_address("2001:db8::1")


def test_validate_link_local_rejects_ipv4() -> None:
    with pytest.raises(ValueError):
        validate_ipv6_link_local_address("192.168.1.1")


def test_validate_link_local_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        validate_ipv6_link_local_address("")


def test_validate_link_local_require_zone_ok() -> None:
    assert validate_ipv6_link_local_address("fe80::1%eth0", require_zone=True) == "fe80::1%eth0"


def test_validate_link_local_require_zone_missing() -> None:
    with pytest.raises(ValueError, match="zone"):
        validate_ipv6_link_local_address("fe80::1", require_zone=True)


def test_validate_link_local_empty_zone() -> None:
    with pytest.raises(ValueError, match="empty"):
        validate_ipv6_link_local_address("fe80::1%")
