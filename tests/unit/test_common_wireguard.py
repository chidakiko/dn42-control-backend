from __future__ import annotations

"""WireGuard 密钥与 endpoint 字符串校验的单元测试。

错误的密钥或 endpoint 会在 ``wg setconf`` 阶段才被 WireGuard 发现，
这会导致 reconcile 循环反复失败。因此 schema 层就由以下两个 helper 抓住问题：

* ``validate_wireguard_key`` / ``is_wireguard_key``：限定 44 个字符、仅
  base64 字母表 + 末尾必须是 ``=``（即可解码为准确 32 字节），
  并拒绝 ``bytes`` / ``None`` 等非字符串输入。
* ``validate_wireguard_endpoint``：接受 IPv4:port、主机名:port、IPv6
  ``[addr]:port``。拒绝：缺端口 / 空端口 / 端口越界、0 或
  65536） / 非数字端口 / IPv6 忘带方括号 / host 空缺。
"""

import pytest

from dn42_common import (
    is_wireguard_key,
    validate_wireguard_endpoint,
    validate_wireguard_key,
)


# 真实 dummy key（来自仓库 render-local-two-node 样例）
_VALID_KEY = "+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ="


class TestValidateWireguardKey:
    def test_accepts_valid_key(self) -> None:
        assert validate_wireguard_key(_VALID_KEY) == _VALID_KEY

    def test_rejects_wrong_length(self) -> None:
        with pytest.raises(ValueError, match="44 characters"):
            validate_wireguard_key("short=")

    def test_rejects_invalid_alphabet(self) -> None:
        bad = "G" * 43 + "*"
        with pytest.raises(ValueError, match="base64 alphabet"):
            validate_wireguard_key(bad)

    def test_rejects_missing_padding(self) -> None:
        bad = "G" * 44
        with pytest.raises(ValueError, match="base64 alphabet"):
            validate_wireguard_key(bad)

    def test_rejects_non_str(self) -> None:
        with pytest.raises(TypeError):
            validate_wireguard_key(b"x" * 44)  # type: ignore[arg-type]


class TestIsWireguardKey:
    def test_truthy(self) -> None:
        assert is_wireguard_key(_VALID_KEY) is True

    def test_falsy_for_garbage(self) -> None:
        assert is_wireguard_key("not-a-key") is False
        assert is_wireguard_key(None) is False
        assert is_wireguard_key(b"x" * 44) is False


class TestValidateWireguardEndpoint:
    @pytest.mark.parametrize(
        "value",
        [
            "203.0.113.1:51820",
            "peer.example.dn42:51820",
            "host:1",
            "host:65535",
            "[fdce:1111:2222::1]:51820",
        ],
    )
    def test_valid(self, value: str) -> None:
        assert validate_wireguard_endpoint(value) == value

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "203.0.113.1",  # missing port
            "203.0.113.1:",  # empty port
            "host:0",  # port out of range
            "host:65536",
            "host:abc",
            "fdce:1111::1:51820",  # ipv6 missing brackets
            ":51820",
        ],
    )
    def test_invalid(self, value: str) -> None:
        with pytest.raises(ValueError):
            validate_wireguard_endpoint(value)
