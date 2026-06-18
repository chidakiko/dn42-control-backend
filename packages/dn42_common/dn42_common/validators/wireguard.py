from __future__ import annotations

"""WireGuard 密钥与端点的格式校验。

WireGuard 把 Curve25519 公钥/私钥/PSK 都序列化成长度 44 的 base64 字符串
（32 字节裸密钥 + base64 = 44 字符，最后一位必为 `=`）。本模块只做格式
校验，不验证私钥与公钥之间的数学关系。
"""

import base64
import binascii
import re


_WG_KEY_LENGTH = 44  # 32 bytes base64 -> 44 chars (含一个 '=' padding)
_WG_KEY_PATTERN = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def validate_wireguard_key(value: str) -> str:
    """校验 WireGuard 公钥 / 私钥 / PSK 字面量是否为合法的 base64 密钥。

    返回原值。失败抛 `ValueError`，错误信息中不包含密钥本身，避免在日志
    里泄露材料。
    """

    if not isinstance(value, str):
        raise TypeError(f"wireguard key must be str, got {type(value).__name__}")
    if len(value) != _WG_KEY_LENGTH:
        raise ValueError(
            f"wireguard key must be exactly {_WG_KEY_LENGTH} characters of base64"
        )
    if not _WG_KEY_PATTERN.fullmatch(value):
        raise ValueError("wireguard key contains characters outside base64 alphabet")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("wireguard key is not valid base64") from exc
    if len(decoded) != 32:
        raise ValueError("wireguard key does not decode to 32 bytes")
    return value


def is_wireguard_key(value: object) -> bool:
    """与 `validate_wireguard_key` 等价的非抛出版本。"""

    if not isinstance(value, str):
        return False
    try:
        validate_wireguard_key(value)
    except (ValueError, TypeError):
        return False
    return True


_WG_ENDPOINT_HOST_PORT = re.compile(
    r"""
    ^
    (?:
        \[(?P<v6>[0-9A-Fa-f:%.]+)\]      # 字面 IPv6 用 [..]
        | (?P<host>[A-Za-z0-9._-]+)      # 主机名或 IPv4
    )
    :(?P<port>\d{1,5})
    $
    """,
    re.VERBOSE,
)


def validate_wireguard_endpoint(value: str) -> str:
    """校验 `host:port` / `[ipv6]:port` 形式的 WireGuard endpoint。

    主机名只在字符集层面校验；具体 DNS 解析由 wg-quick / 节点负责。
    """

    if not isinstance(value, str) or not value:
        raise ValueError("wireguard endpoint must be a non-empty string")
    match = _WG_ENDPOINT_HOST_PORT.match(value)
    if not match:
        raise ValueError(f"{value!r} is not a valid wireguard endpoint")
    port = int(match.group("port"))
    if not 1 <= port <= 65535:
        raise ValueError(f"wireguard endpoint port {port} out of range")
    return value


__all__ = [
    "is_wireguard_key",
    "validate_wireguard_endpoint",
    "validate_wireguard_key",
]
