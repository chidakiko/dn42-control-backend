from __future__ import annotations

"""跨包共享的轻量校验工具。

按主题拆分到子模块：

- `ip` —— 通用 IPv4/IPv6 地址、前缀、接口地址
- `asn` —— ASN 范围与私有 ASN 判定
- `timestamp` —— ISO-8601 时间戳
- `dn42_space` —— DN42 注册库地址空间常量与严格校验器
- `wireguard` —— WireGuard 密钥 / endpoint 格式
- `domain` —— DNS 域名 / 主机名

本 ``__init__`` 是 validators 子包的聚合出口：把各子模块的 API 汇总在一处，
上层 ``dn42_common`` 顶层包据此 re-export（``from dn42_common import validate_ip_address``，
这是规范用法）。
"""

from .asn import (
    PRIVATE_ASN_16BIT_RANGE,
    PRIVATE_ASN_32BIT_RANGE,
    is_private_asn,
    validate_asn,
)
from .agent_token import (
    is_agent_token,
    validate_agent_token,
)
from .dn42_space import (
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
from .ip import (
    is_address_in_prefix,
    is_ipv6_link_local,
    split_ipv6_zone,
    validate_ip_address,
    validate_ip_address_with_optional_zone,
    validate_ip_interface,
    validate_ip_network,
    validate_ipv6_link_local_address,
)
from .domain import (
    is_dn42_zone,
    is_domain_name,
    validate_domain_name,
    validate_hostname,
)
from .timestamp import validate_iso8601_timestamp
from .wireguard import (
    is_wireguard_key,
    validate_wireguard_endpoint,
    validate_wireguard_key,
)


__all__ = [
    "DN42_ANYCAST_IPV4_PREFIXES",
    "DN42_ANYCAST_IPV6_SPACE",
    "DN42_CLOSED_IPV4_PREFIXES",
    "DN42_IPV4_SPACE",
    "DN42_IPV6_SPACE",
    "DN42_RESERVED_IPV4_PREFIXES",
    "DN42_TRANSFER_IPV4_PREFIXES",
    "PRIVATE_ASN_16BIT_RANGE",
    "PRIVATE_ASN_32BIT_RANGE",
    "is_address_in_prefix",
    "is_agent_token",
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
    "is_dn42_zone",
    "is_domain_name",
    "is_ipv6_link_local",
    "is_private_asn",
    "is_wireguard_key",
    "split_ipv6_zone",
    "validate_asn",
    "validate_agent_token",
    "validate_dn42_ipv4_network",
    "validate_dn42_ipv6_network",
    "validate_domain_name",
    "validate_hostname",
    "validate_ip_address",
    "validate_ip_address_with_optional_zone",
    "validate_ip_interface",
    "validate_ip_network",
    "validate_ipv6_link_local_address",
    "validate_iso8601_timestamp",
    "validate_wireguard_endpoint",
    "validate_wireguard_key",
]
