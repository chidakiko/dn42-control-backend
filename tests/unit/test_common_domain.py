from __future__ import annotations

"""主机名 / 域名 / DN42 zone 字符串校验函数的单元测试。

覆盖 :mod:`dn42_common` 导出的四个 helper：

* ``validate_domain_name`` 默认要求多 label、仅允许 LDH，接受尾部
  ``.``、拒绝 label 越过 63 字符与 RFC 1035 总长 255 限制；为
  ACME 场景提供 ``allow_underscore=True``，为主机名场景提供
  ``require_multi_label=False`` / ``allow_trailing_dot=False`` 开关。
* ``validate_hostname`` 是单 label 变体，拒绝 FQDN 与领头连字符。
* ``is_domain_name`` 与 ``is_dn42_zone`` 是允许传入任意类型的布尔
  包装，该套用例覆盖了 ``.dn42``、``.neo``、``in-addr.arpa``、
  ``ip6.arpa`` 等被 DN42 社区认可的 zone 分类。
"""

import pytest

from dn42_common import (
    is_dn42_zone,
    is_domain_name,
    validate_domain_name,
    validate_hostname,
)


class TestValidateDomainName:
    @pytest.mark.parametrize(
        "value",
        [
            "example.dn42",
            "router.hkg1.example.dn42",
            "example.dn42.",  # 允许 FQDN 根
            "20.172.in-addr.arpa",
            "2.4.d.f.ip6.arpa",
            "a-b.c-d.dn42",
            "a" * 63 + ".dn42",
        ],
    )
    def test_accepts(self, value: str) -> None:
        assert validate_domain_name(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            ".",
            "single",  # 默认要求多段
            "-leadiexample.dn42",
            "trailing-.dn42",
            "label..dn42",
            "label space.dn42",
            "a" * 64 + ".dn42",  # 单 label 超过 63
            "_underscore.dn42",  # 默认不接受 underscore
            "中文.dn42",
        ],
    )
    def test_rejects(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_domain_name(value)

    def test_total_length_limit(self) -> None:
        too_long = ".".join(["a" * 50] * 6)  # 6*50 + 5 = 305 字符
        with pytest.raises(ValueError, match="exceeds RFC 1035"):
            validate_domain_name(too_long)

    def test_disallow_trailing_dot(self) -> None:
        with pytest.raises(ValueError, match="must not end with"):
            validate_domain_name("example.dn42.", allow_trailing_dot=False)

    def test_allow_underscore(self) -> None:
        assert (
            validate_domain_name("_acme-challenge.example.dn42", allow_underscore=True)
            == "_acme-challenge.example.dn42"
        )

    def test_single_label_when_allowed(self) -> None:
        assert (
            validate_domain_name("router", require_multi_label=False) == "router"
        )

    def test_allow_slash_rfc2317_reverse_zone(self) -> None:
        # RFC 2317 无类反向委派 zone 名标签含 `/`（如 0/26.0.20.172.in-addr.arpa）。
        zone = "0/26.0.20.172.in-addr.arpa"
        assert validate_domain_name(zone, require_multi_label=False, allow_slash=True) == zone

    def test_slash_rejected_by_default(self) -> None:
        with pytest.raises(ValueError):
            validate_domain_name("0/26.0.20.172.in-addr.arpa")


class TestValidateHostname:
    @pytest.mark.parametrize("value", ["router", "edge1", "node-1"])
    def test_accepts(self, value: str) -> None:
        assert validate_hostname(value) == value

    @pytest.mark.parametrize("value", ["router.dn42", "router.", "-bad"])
    def test_rejects(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_hostname(value)


class TestIsDomainName:
    def test_truthy(self) -> None:
        assert is_domain_name("example.dn42") is True

    def test_falsy(self) -> None:
        assert is_domain_name("not a domain") is False
        assert is_domain_name(None) is False
        assert is_domain_name(123) is False

    def test_kwargs_passed_through(self) -> None:
        assert is_domain_name("router", require_multi_label=False) is True
        assert is_domain_name("router") is False


class TestIsDn42Zone:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("example.dn42", True),
            ("example.dn42.", True),
            ("example.neo", True),
            ("20.172.in-addr.arpa", True),
            ("2.4.d.f.ip6.arpa", True),
            ("example.com", False),
            ("not a domain", False),
            ("", False),
        ],
    )
    def test_classification(self, value: str, expected: bool) -> None:
        assert is_dn42_zone(value) is expected
