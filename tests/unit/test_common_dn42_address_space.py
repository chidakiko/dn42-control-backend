from __future__ import annotations

"""DN42 地址空间常量、分类函数与网络隐含关系校验的单元测试。

覆盖 :mod:`dn42_common` 导出的以下几组能力：

* 常量表 ``DN42_IPV4_SPACE`` / ``DN42_IPV6_SPACE`` 及四类特殊用途前缀
  （anycast / transfer / closed / reserved）与 DN42 wiki 当前公布的划
  分保持一致。
* ``is_dn42_ipv4_address`` / ``is_dn42_ipv6_address`` / ``is_dn42_address``
  只接受资产公告范围内的地址；IPv4 helper 拒绝 v6，反之亦然；
  非法字串返回 ``False`` 而不抛异常。
* ``is_dn42_*_network`` 对子网的包含判定：仅接受完全落在
  主网中的 prefix，包含主网本身但拒绝 “过于宽松” 的 supernet。
* 专项识别函数 ``is_dn42_anycast_*`` / ``is_dn42_transfer_network`` /
  ``is_dn42_closed_network`` / ``is_dn42_reserved_network`` 边界。
* ``validate_dn42_ipv4_network`` / ``validate_dn42_ipv6_network`` 的多种拒
  绝原因（越界 / closed / reserved / anycast 禁用 / transfer 禁用 /
  版本不匹配 / 语法错误）会产生可预测的错误信息。
"""

import pytest

from dn42_common import (
    DN42_ANYCAST_IPV4_PREFIXES,
    DN42_ANYCAST_IPV6_SPACE,
    DN42_CLOSED_IPV4_PREFIXES,
    DN42_IPV4_SPACE,
    DN42_IPV6_SPACE,
    DN42_RESERVED_IPV4_PREFIXES,
    DN42_TRANSFER_IPV4_PREFIXES,
    is_dn42_address,
    is_dn42_anycast_address,
    is_dn42_anycast_network,
    is_dn42_closed_network,
    is_dn42_ipv4_address,
    is_dn42_ipv4_network,
    is_dn42_ipv6_address,
    is_dn42_ipv6_network,
    is_dn42_network,
    is_dn42_reserved_network,
    is_dn42_transfer_network,
    validate_dn42_ipv4_network,
    validate_dn42_ipv6_network,
)


class TestDn42SpaceConstants:
    def test_ipv4_supernet(self) -> None:
        assert str(DN42_IPV4_SPACE) == "172.20.0.0/14"

    def test_ipv6_supernet(self) -> None:
        assert str(DN42_IPV6_SPACE) == "fd00::/8"

    def test_anycast_v4_includes_four_slash_24(self) -> None:
        assert {str(p) for p in DN42_ANYCAST_IPV4_PREFIXES} == {
            "172.20.0.0/24",
            "172.21.0.0/24",
            "172.22.0.0/24",
            "172.23.0.0/24",
        }

    def test_anycast_v6_block(self) -> None:
        assert str(DN42_ANYCAST_IPV6_SPACE) == "fd42:d42:d42::/48"

    def test_transfer_v4(self) -> None:
        assert {str(p) for p in DN42_TRANSFER_IPV4_PREFIXES} == {
            "172.20.240.0/20",
            "172.22.240.0/20",
        }

    def test_closed_v4(self) -> None:
        assert {str(p) for p in DN42_CLOSED_IPV4_PREFIXES} == {"172.23.16.0/21"}

    def test_reserved_v4(self) -> None:
        assert {str(p) for p in DN42_RESERVED_IPV4_PREFIXES} == {
            "172.21.0.0/18",
            "172.21.128.0/17",
            "172.22.192.0/18",
        }


class TestIsDn42Address:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("172.20.0.1", True),
            ("172.23.255.255", True),
            ("172.19.255.255", False),
            ("172.24.0.0", False),
            ("10.0.0.1", False),
            ("not-an-ip", False),
            ("fd00::1", False),  # ipv4 helper rejects v6
        ],
    )
    def test_ipv4(self, value: str, expected: bool) -> None:
        assert is_dn42_ipv4_address(value) is expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("fd00::1", True),
            ("fdce:1111:2222::1", True),
            ("fe80::1", False),
            ("2001:db8::1", False),
            ("not-an-ip", False),
            ("172.20.0.1", False),
        ],
    )
    def test_ipv6(self, value: str, expected: bool) -> None:
        assert is_dn42_ipv6_address(value) is expected

    def test_combined(self) -> None:
        assert is_dn42_address("172.20.0.1") is True
        assert is_dn42_address("fd00::1") is True
        assert is_dn42_address("8.8.8.8") is False


class TestIsDn42Network:
    def test_ipv4_inside(self) -> None:
        assert is_dn42_ipv4_network("172.20.0.0/26") is True

    def test_ipv4_supernet_itself(self) -> None:
        assert is_dn42_ipv4_network("172.20.0.0/14") is True

    def test_ipv4_too_loose(self) -> None:
        assert is_dn42_ipv4_network("172.16.0.0/12") is False

    def test_ipv4_outside(self) -> None:
        assert is_dn42_ipv4_network("10.0.0.0/24") is False

    def test_ipv6_inside(self) -> None:
        assert is_dn42_ipv6_network("fdce:1111:2222::/48") is True

    def test_ipv6_outside(self) -> None:
        assert is_dn42_ipv6_network("fe80::/10") is False

    def test_combined(self) -> None:
        assert is_dn42_network("172.20.0.0/26") is True
        assert is_dn42_network("fdce:1111:2222::/48") is True
        assert is_dn42_network("10.0.0.0/24") is False

    def test_invalid_returns_false(self) -> None:
        assert is_dn42_ipv4_network("not-a-prefix") is False
        assert is_dn42_ipv6_network("not-a-prefix") is False


class TestSpecialUseClassification:
    def test_anycast_address(self) -> None:
        assert is_dn42_anycast_address("172.20.0.5") is True
        assert is_dn42_anycast_address("172.21.0.7") is True
        assert is_dn42_anycast_address("172.20.1.1") is False
        assert is_dn42_anycast_address("fd42:d42:d42::1") is True
        assert is_dn42_anycast_address("fdce:1111:2222::1") is False

    def test_anycast_network(self) -> None:
        assert is_dn42_anycast_network("172.22.0.0/24") is True
        assert is_dn42_anycast_network("172.22.0.0/26") is True
        assert is_dn42_anycast_network("172.22.0.0/23") is False  # 跨出 anycast /24
        assert is_dn42_anycast_network("fd42:d42:d42::/64") is True

    def test_transfer(self) -> None:
        assert is_dn42_transfer_network("172.20.240.0/24") is True
        assert is_dn42_transfer_network("172.22.241.0/30") is True
        assert is_dn42_transfer_network("172.20.0.0/26") is False

    def test_closed(self) -> None:
        assert is_dn42_closed_network("172.23.16.0/21") is True
        assert is_dn42_closed_network("172.23.20.0/24") is True
        assert is_dn42_closed_network("172.23.24.0/24") is False

    def test_reserved(self) -> None:
        assert is_dn42_reserved_network("172.21.0.0/18") is True
        assert is_dn42_reserved_network("172.21.128.0/17") is True
        assert is_dn42_reserved_network("172.22.192.0/18") is True
        assert is_dn42_reserved_network("172.20.0.0/24") is False


class TestValidateDn42Ipv4Network:
    def test_accepts_user_prefix(self) -> None:
        assert validate_dn42_ipv4_network("172.20.0.0/26") == "172.20.0.0/26"

    def test_rejects_outside_supernet(self) -> None:
        with pytest.raises(ValueError, match="outside dn42 IPv4 space"):
            validate_dn42_ipv4_network("10.0.0.0/24")

    def test_rejects_loose_supernet(self) -> None:
        with pytest.raises(ValueError, match="outside dn42 IPv4 space"):
            validate_dn42_ipv4_network("172.16.0.0/12")

    def test_rejects_closed(self) -> None:
        with pytest.raises(ValueError, match="closed allocation"):
            validate_dn42_ipv4_network("172.23.16.0/24")

    def test_rejects_reserved(self) -> None:
        with pytest.raises(ValueError, match="reserved-for-future"):
            validate_dn42_ipv4_network("172.21.0.0/24")

    def test_rejects_anycast_when_disallowed(self) -> None:
        with pytest.raises(ValueError, match="anycast"):
            validate_dn42_ipv4_network("172.20.0.0/24", allow_anycast=False)

    def test_anycast_allowed_by_default(self) -> None:
        assert validate_dn42_ipv4_network("172.20.0.0/24") == "172.20.0.0/24"

    def test_rejects_transfer_when_disallowed(self) -> None:
        with pytest.raises(ValueError, match="transfer-network"):
            validate_dn42_ipv4_network("172.20.240.0/30", allow_transfer=False)

    def test_rejects_ipv6(self) -> None:
        with pytest.raises(ValueError, match="not an IPv4 network"):
            validate_dn42_ipv4_network("fd00::/8")

    def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError):
            validate_dn42_ipv4_network("not-a-prefix")


class TestValidateDn42Ipv6Network:
    def test_accepts_ula_prefix(self) -> None:
        assert validate_dn42_ipv6_network("fdce:1111:2222::/48") == "fdce:1111:2222::/48"

    def test_rejects_outside(self) -> None:
        with pytest.raises(ValueError, match="outside dn42 IPv6 space"):
            validate_dn42_ipv6_network("2001:db8::/32")

    def test_rejects_link_local(self) -> None:
        with pytest.raises(ValueError, match="outside dn42 IPv6 space"):
            validate_dn42_ipv6_network("fe80::/10")

    def test_rejects_anycast_when_disallowed(self) -> None:
        with pytest.raises(ValueError, match="anycast"):
            validate_dn42_ipv6_network("fd42:d42:d42::/64", allow_anycast=False)

    def test_anycast_allowed_by_default(self) -> None:
        assert (
            validate_dn42_ipv6_network("fd42:d42:d42::/64") == "fd42:d42:d42::/64"
        )

    def test_rejects_ipv4(self) -> None:
        with pytest.raises(ValueError, match="not an IPv6 network"):
            validate_dn42_ipv6_network("172.20.0.0/24")
