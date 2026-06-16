from __future__ import annotations

"""DN42 地址空间常量、谓词与严格校验器。

来源：https://dn42.eu/Address-Space

这里只描述注册库中已经写明的策略段；用于在 schema 层拦截明显不属于
DN42 的前缀，以及在告警/工具里识别特殊用途的子段。
"""

from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)


# ----- 常量 -----

DN42_IPV4_SPACE: IPv4Network = IPv4Network("172.20.0.0/14")
DN42_IPV6_SPACE: IPv6Network = IPv6Network("fd00::/8")

# 节点专用 anycast 服务地址段（每段一个 /24）
DN42_ANYCAST_IPV4_PREFIXES: tuple[IPv4Network, ...] = (
    IPv4Network("172.20.0.0/24"),
    IPv4Network("172.21.0.0/24"),
    IPv4Network("172.22.0.0/24"),
    IPv4Network("172.23.0.0/24"),
)
DN42_ANYCAST_IPV6_SPACE: IPv6Network = IPv6Network("fd42:d42:d42::/48")

# 跨节点 transfer 网段（点对点链路）
DN42_TRANSFER_IPV4_PREFIXES: tuple[IPv4Network, ...] = (
    IPv4Network("172.20.240.0/20"),
    IPv4Network("172.22.240.0/20"),
)

# 不再分配的范围
DN42_CLOSED_IPV4_PREFIXES: tuple[IPv4Network, ...] = (
    IPv4Network("172.23.16.0/21"),
)

# 预留给未来使用的范围
DN42_RESERVED_IPV4_PREFIXES: tuple[IPv4Network, ...] = (
    IPv4Network("172.21.0.0/18"),
    IPv4Network("172.21.128.0/17"),
    IPv4Network("172.22.192.0/18"),
)


# ----- 内部工具 -----


def _network_inside(
    network: IPv4Network | IPv6Network, supernet: IPv4Network | IPv6Network
) -> bool:
    if isinstance(network, IPv4Network) and isinstance(supernet, IPv4Network):
        return network.subnet_of(supernet)
    if isinstance(network, IPv6Network) and isinstance(supernet, IPv6Network):
        return network.subnet_of(supernet)
    return False


def _matches_any(
    value: str, prefixes: tuple[IPv4Network | IPv6Network, ...]
) -> bool:
    try:
        net = ip_network(value, strict=False)
    except ValueError:
        return False
    return any(_network_inside(net, p) for p in prefixes)


# ----- 地址 / 前缀谓词 -----


def is_dn42_ipv4_address(value: str) -> bool:
    """是否为落在 dn42 IPv4 空间（172.20.0.0/14）内的地址。"""

    try:
        addr = ip_address(value)
    except ValueError:
        return False
    return isinstance(addr, IPv4Address) and addr in DN42_IPV4_SPACE


def is_dn42_ipv6_address(value: str) -> bool:
    """是否为落在 dn42 IPv6 空间（fd00::/8 ULA）内的地址。"""

    try:
        addr = ip_address(value)
    except ValueError:
        return False
    return isinstance(addr, IPv6Address) and addr in DN42_IPV6_SPACE


def is_dn42_address(value: str) -> bool:
    """是否为 dn42 IPv4 / IPv6 地址（任一）。"""

    return is_dn42_ipv4_address(value) or is_dn42_ipv6_address(value)


def is_dn42_ipv4_network(value: str) -> bool:
    """前缀是否完全位于 dn42 IPv4 空间内。"""

    try:
        net = ip_network(value, strict=False)
    except ValueError:
        return False
    return isinstance(net, IPv4Network) and _network_inside(net, DN42_IPV4_SPACE)


def is_dn42_ipv6_network(value: str) -> bool:
    """前缀是否完全位于 dn42 IPv6 空间内。"""

    try:
        net = ip_network(value, strict=False)
    except ValueError:
        return False
    return isinstance(net, IPv6Network) and _network_inside(net, DN42_IPV6_SPACE)


def is_dn42_network(value: str) -> bool:
    """IPv4/IPv6 任一是否落在 dn42 空间。"""

    return is_dn42_ipv4_network(value) or is_dn42_ipv6_network(value)


# ----- 特殊用途段判定 -----


def is_dn42_anycast_address(value: str) -> bool:
    """地址是否落在 dn42 anycast 段（IPv4 四个 /24 或 fd42:d42:d42::/48）。"""

    try:
        addr = ip_address(value)
    except ValueError:
        return False
    if isinstance(addr, IPv4Address):
        return any(addr in p for p in DN42_ANYCAST_IPV4_PREFIXES)
    return addr in DN42_ANYCAST_IPV6_SPACE


def is_dn42_anycast_network(value: str) -> bool:
    """前缀是否完全位于 dn42 anycast 段内。"""

    if _matches_any(value, DN42_ANYCAST_IPV4_PREFIXES):
        return True
    return _matches_any(value, (DN42_ANYCAST_IPV6_SPACE,))


def is_dn42_transfer_network(value: str) -> bool:
    """前缀是否位于 dn42 transfer 段内（点对点链路）。"""

    return _matches_any(value, DN42_TRANSFER_IPV4_PREFIXES)


def is_dn42_closed_network(value: str) -> bool:
    """前缀是否位于不再分配的 dn42 段内。"""

    return _matches_any(value, DN42_CLOSED_IPV4_PREFIXES)


def is_dn42_reserved_network(value: str) -> bool:
    """前缀是否位于 dn42 预留段内（未来使用）。"""

    return _matches_any(value, DN42_RESERVED_IPV4_PREFIXES)


# ----- 严格校验器 -----


def validate_dn42_ipv4_network(
    value: str,
    *,
    allow_anycast: bool = True,
    allow_transfer: bool = True,
) -> str:
    """要求前缀完全位于 dn42 IPv4 空间内，并默认拒绝 closed/reserved 段。

    `allow_anycast` / `allow_transfer` 默认为 True，调用方可以在描述
    "节点自有用户前缀" 等更严格的场景下手动收紧。
    """

    network = ip_network(value, strict=False)
    if not isinstance(network, IPv4Network):
        raise ValueError(f"{value!r} is not an IPv4 network")
    if not _network_inside(network, DN42_IPV4_SPACE):
        raise ValueError(f"{value!r} is outside dn42 IPv4 space {DN42_IPV4_SPACE}")
    if is_dn42_closed_network(str(network)):
        raise ValueError(f"{value!r} is in a dn42 closed allocation range")
    if is_dn42_reserved_network(str(network)):
        raise ValueError(f"{value!r} is in a dn42 reserved-for-future range")
    if not allow_anycast and is_dn42_anycast_network(str(network)):
        raise ValueError(f"{value!r} is in a dn42 anycast range")
    if not allow_transfer and is_dn42_transfer_network(str(network)):
        raise ValueError(f"{value!r} is in a dn42 transfer-network range")
    return str(network)


def validate_dn42_ipv6_network(value: str, *, allow_anycast: bool = True) -> str:
    """要求前缀完全位于 dn42 IPv6 ULA 空间内（fd00::/8）。"""

    network = ip_network(value, strict=False)
    if not isinstance(network, IPv6Network):
        raise ValueError(f"{value!r} is not an IPv6 network")
    if not _network_inside(network, DN42_IPV6_SPACE):
        raise ValueError(f"{value!r} is outside dn42 IPv6 space {DN42_IPV6_SPACE}")
    if not allow_anycast and is_dn42_anycast_network(str(network)):
        raise ValueError(f"{value!r} is in the dn42 anycast range")
    return str(network)


__all__ = [
    "DN42_ANYCAST_IPV4_PREFIXES",
    "DN42_ANYCAST_IPV6_SPACE",
    "DN42_CLOSED_IPV4_PREFIXES",
    "DN42_IPV4_SPACE",
    "DN42_IPV6_SPACE",
    "DN42_RESERVED_IPV4_PREFIXES",
    "DN42_TRANSFER_IPV4_PREFIXES",
    "is_dn42_address",
    "is_dn42_anycast_address",
    "is_dn42_anycast_network",
    "is_dn42_closed_network",
    "is_dn42_ipv4_address",
    "is_dn42_ipv4_network",
    "is_dn42_ipv6_address",
    "is_dn42_ipv6_network",
    "is_dn42_network",
    "is_dn42_reserved_network",
    "is_dn42_transfer_network",
    "validate_dn42_ipv4_network",
    "validate_dn42_ipv6_network",
]
