from __future__ import annotations

""":mod:`dn42_common.validators` 通用校验库的单元测试。

该库被 schemas、runtime、agent 三处复用，变动影响面较广，因此测试
覆盖面需要足够稠密：

* IP / 网络 / 接口地址：``validate_ip_address`` 可选 ``version``、
  ``validate_ip_network`` 提供 ``strict`` 模式拒绝 “主机位未清零”、
  ``validate_ip_interface`` 接受 ``addr/prefix`` 形式。
* IPv6 zone：``split_ipv6_zone`` 拆分 ``addr%zone``；
  ``validate_ip_address_with_optional_zone`` 仅在 link-local 地址上接受
  zone，并拒绝空 zone。
* 子网包含：``is_address_in_prefix`` 在版本不匹配 / 非法输入下
  返回 ``False`` 而非抛异常。
* ASN：``validate_asn`` 拒绝 0 / 负数 / 超过 32 位上限 / 非 ``int``
  （包括 ``bool`` 和 ``str``）；``is_private_asn`` 识别 16/32 位私有
  ASN 范围。
* ISO-8601 时间戳：接受带 / 不带微秒、带 ``Z`` 或明示偏移。
* 节点 agent token：限制 “22 ≤ len ≤ 512、仅 base64url 安全字符”，
  随身提供 ``is_agent_token`` 作为合法性探针。
"""

import pytest

from dn42_common import (
    is_address_in_prefix,
    is_agent_token,
    is_private_asn,
    split_ipv6_zone,
    validate_agent_token,
    validate_asn,
    validate_ip_address,
    validate_ip_address_with_optional_zone,
    validate_ip_interface,
    validate_ip_network,
    validate_iso8601_timestamp,
)


class TestValidateIpAddress:
    @pytest.mark.parametrize("value", ["10.0.0.1", "192.168.1.255", "fd00::1", "::1"])
    def test_accepts_valid(self, value: str) -> None:
        assert validate_ip_address(value) == value

    def test_rejects_invalid(self) -> None:
        with pytest.raises(ValueError):
            validate_ip_address("not-an-ip")

    def test_version_mismatch(self) -> None:
        with pytest.raises(ValueError):
            validate_ip_address("10.0.0.1", version=6)
        with pytest.raises(ValueError):
            validate_ip_address("fd00::1", version=4)


class TestValidateIpNetwork:
    def test_accepts_v4_and_v6(self) -> None:
        assert validate_ip_network("172.20.0.0/14") == "172.20.0.0/14"
        assert validate_ip_network("fd00::/8") == "fd00::/8"

    def test_strict_mode_rejects_host_bits_set(self) -> None:
        with pytest.raises(ValueError):
            validate_ip_network("10.0.0.5/24", strict=True)


class TestValidateIpInterface:
    def test_accepts_addr_with_prefix(self) -> None:
        assert validate_ip_interface("10.0.0.1/24") == "10.0.0.1/24"


class TestSplitIpv6Zone:
    def test_returns_zone_when_present(self) -> None:
        assert split_ipv6_zone("fe80::1%eth0") == ("fe80::1", "eth0")

    def test_returns_none_when_absent(self) -> None:
        assert split_ipv6_zone("fd00::1") == ("fd00::1", None)


class TestValidateIpAddressWithOptionalZone:
    def test_accepts_link_local_with_zone(self) -> None:
        assert validate_ip_address_with_optional_zone("fe80::1%wg0") == "fe80::1%wg0"

    def test_accepts_plain_address(self) -> None:
        assert validate_ip_address_with_optional_zone("fd00::1") == "fd00::1"

    def test_rejects_empty_zone(self) -> None:
        with pytest.raises(ValueError):
            validate_ip_address_with_optional_zone("fe80::1%")


class TestIsAddressInPrefix:
    def test_v4_match(self) -> None:
        assert is_address_in_prefix("172.20.0.5", "172.20.0.0/14") is True

    def test_v4_mismatch(self) -> None:
        assert is_address_in_prefix("10.0.0.1", "172.20.0.0/14") is False

    def test_v6_match(self) -> None:
        assert is_address_in_prefix("fd00::1", "fd00::/8") is True

    def test_version_mismatch_returns_false(self) -> None:
        assert is_address_in_prefix("fd00::1", "172.20.0.0/14") is False

    def test_invalid_inputs_return_false(self) -> None:
        assert is_address_in_prefix("not-ip", "172.20.0.0/14") is False
        assert is_address_in_prefix("10.0.0.1", "not-prefix") is False


class TestValidateAsn:
    @pytest.mark.parametrize("value", [1, 64512, 4_242_423_914, 4_294_967_295])
    def test_accepts_valid(self, value: int) -> None:
        assert validate_asn(value) == value

    @pytest.mark.parametrize("value", [0, -1, 4_294_967_296])
    def test_rejects_out_of_range(self, value: int) -> None:
        with pytest.raises(ValueError):
            validate_asn(value)

    def test_rejects_non_int(self) -> None:
        with pytest.raises(TypeError):
            validate_asn("64512")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            validate_asn(True)  # type: ignore[arg-type]


class TestIsPrivateAsn:
    @pytest.mark.parametrize("value", [64512, 65000, 65534, 4_200_000_000, 4_242_423_914])
    def test_recognises_private_ranges(self, value: int) -> None:
        assert is_private_asn(value) is True

    @pytest.mark.parametrize("value", [1, 64511, 65535, 4_199_999_999, 4_294_967_295])
    def test_rejects_public(self, value: int) -> None:
        assert is_private_asn(value) is False


class TestValidateIso8601Timestamp:
    @pytest.mark.parametrize(
        "value",
        [
            "2026-06-03T12:00:00",
            "2026-06-03T12:00:00+00:00",
            "2026-06-03T12:00:00Z",
            "2026-06-03T12:00:00.123456+00:00",
        ],
    )
    def test_accepts_valid(self, value: str) -> None:
        assert validate_iso8601_timestamp(value) == value

    @pytest.mark.parametrize("value", ["", "not-a-date", "2026/06/03"])
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_iso8601_timestamp(value)


class TestValidateAgentToken:
    @pytest.mark.parametrize(
        "value",
        [
            "abcdefghijklmnopqrstuv",  # 恰好 22 字符
            "Zm9vYmFyLWJhei1xdXV4LXRva2Vu",
            "a-b_C0d1E2f3G4h5I6j7K8",
            "x" * 512,
        ],
    )
    def test_accepts_valid(self, value: str) -> None:
        assert validate_agent_token(value) == value
        assert is_agent_token(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "tooshort",  # 长度不足
            "enroll",
            "mvp-agent-token",  # 15 < 22
            "has space inside the token!!",
            "contains+slash/and=padding====",
            "x" * 513,  # 超长
        ],
    )
    def test_rejects_invalid(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_agent_token(value)
        assert is_agent_token(value) is False

    def test_non_str_is_rejected(self) -> None:
        with pytest.raises(TypeError):
            validate_agent_token(None)  # type: ignore[arg-type]
        assert is_agent_token(None) is False
