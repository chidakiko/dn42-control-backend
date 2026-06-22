from __future__ import annotations

"""通用 IP 地址 / 前缀 / 接口地址校验。

不绑定 DN42 业务，只是把 `ipaddress` 标准库的能力包成会抛 `ValueError`
的纯函数，便于在 Pydantic field validator 里直接调用。
"""

from ipaddress import (
    AddressValueError,
    IPv4Address,
    IPv6Address,
    NetmaskValueError,
    ip_address,
    ip_interface,
    ip_network,
)
from typing import Literal


def validate_ip_address(value: str, *, version: Literal[4, 6, None] = None) -> str:
    """校验是否为合法 IPv4/IPv6 地址，返回规范化字符串。"""

    address = ip_address(value)
    if version == 4 and not isinstance(address, IPv4Address):
        raise ValueError(f"{value!r} is not an IPv4 address")
    if version == 6 and not isinstance(address, IPv6Address):
        raise ValueError(f"{value!r} is not an IPv6 address")
    return str(address)


def validate_ip_network(value: str, *, strict: bool = False) -> str:
    """校验是否为合法 IPv4/IPv6 前缀，返回规范化字符串。"""

    network = ip_network(value, strict=strict)
    return str(network)


def validate_ip_interface(value: str) -> str:
    """校验 `addr/prefix` 形式的接口地址。"""

    interface = ip_interface(value)
    return str(interface)


def split_ipv6_zone(value: str) -> tuple[str, str | None]:
    """拆解 IPv6 link-local 中的 `%zone`，返回 (address, zone | None)。

    若 `value` 中无 `%`，原样返回。地址部分不会被进一步校验。
    """

    if "%" in value:
        address, zone = value.split("%", 1)
        return address, zone
    return value, None


def validate_ip_address_with_optional_zone(value: str) -> str:
    """允许 IPv6 link-local 形如 `fe80::1%eth0` 的地址。"""

    address, zone = split_ipv6_zone(value)
    validate_ip_address(address)
    if zone is not None and not zone:
        raise ValueError(f"{value!r} has empty IPv6 zone identifier")
    return value


def is_address_in_prefix(address: str, prefix: str) -> bool:
    """判断地址是否落在前缀范围内（IPv4/IPv6 自适应）。"""

    try:
        addr = ip_address(address)
        net = ip_network(prefix, strict=False)
    except (AddressValueError, NetmaskValueError, ValueError):
        return False
    if addr.version != net.version:
        return False
    return addr in net


def is_ipv6_link_local(value: str) -> bool:
    """是否为 IPv6 link-local 地址（fe80::/10），自动剥离 `%zone`。

    任何解析失败、IPv4 地址或非 fe80::/10 地址都返回 False。
    """

    if not isinstance(value, str):
        return False
    address, _zone = split_ipv6_zone(value)
    try:
        parsed = ip_address(address)
    except ValueError:
        return False
    return isinstance(parsed, IPv6Address) and parsed.is_link_local


def validate_ipv6_link_local_address(value: str, *, require_zone: bool = False) -> str:
    """校验 fe80::/10 link-local 地址，可选要求带 `%zone`（接口名）。

    返回原值。`require_zone=True` 时若缺少 `%zone` 或 zone 为空则抛错。
    """

    if not isinstance(value, str) or not value:
        raise ValueError("ipv6 link-local must be a non-empty string")
    address, zone = split_ipv6_zone(value)
    parsed = ip_address(address)
    if not isinstance(parsed, IPv6Address) or not parsed.is_link_local:
        raise ValueError(f"{value!r} is not an IPv6 link-local address (fe80::/10)")
    if require_zone and (zone is None or zone == ""):
        raise ValueError(f"{value!r} requires a zone identifier (e.g. '%eth0')")
    if zone is not None and zone == "":
        raise ValueError(f"{value!r} has empty IPv6 zone identifier")
    return value


__all__ = [
    "is_address_in_prefix",
    "is_ipv6_link_local",
    "split_ipv6_zone",
    "validate_ip_address",
    "validate_ip_address_with_optional_zone",
    "validate_ip_interface",
    "validate_ip_network",
    "validate_ipv6_link_local_address",
]
