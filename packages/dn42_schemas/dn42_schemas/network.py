from __future__ import annotations

"""节点身份、接口和 WireGuard 邻居相关的 schema。"""

from dn42_common import (
    Dn42OriginRegionCommunity,
    is_address_in_prefix,
    validate_dn42_ipv4_network,
    validate_dn42_ipv6_network,
    validate_ip_address,
    validate_ip_interface,
    validate_ip_network,
    validate_ipv6_link_local_address,
    validate_wireguard_endpoint,
    validate_wireguard_key,
)
from pydantic import Field, field_validator, model_validator

from .base import StrictModel
from .enums import InterfaceKind


class NodeSpec(StrictModel):
    """节点身份与自有前缀定义。

    Attributes:
        node_id: 节点唯一标识；通常也会作为模板层和 runtime 层的稳定节点名使用。
        site: 站点或机房标识。
        region: DN42 区域枚举值。
        asn: 节点所属 ASN。
        router_id: BIRD router id，通常是稳定的 IPv4 地址。
        ipv4_prefixes: 该节点拥有并可宣告的 IPv4 前缀集合。
        ipv6_prefixes: 该节点拥有并可宣告的 IPv6 前缀集合。
        loopback_ipv4: 节点 loopback IPv4；若设置则必须属于 `ipv4_prefixes` 之一。
        loopback_ipv6: 节点 loopback IPv6；若设置则必须属于 `ipv6_prefixes` 之一。
        link_local: 节点级 IPv6 link-local（`fe80::/10`，不带 `%zone`）。**单一真相源**：一节点
            一个本端 LLA，所有**外部 eBGP** WG 接口复用（WG 建邻 + eBGP-over-link-local 源）。
            materializer 把 `<link_local>/64` 派生到这些接口 `addresses`；存量接口侧不再各存一份
            （配套 backfill 剥离）。渲染器再与各接口 fe80 `peer_route` 配成 `peer` 形式。
            **不含内部互联**：iBGP/OSPF 的内部 WG 接口用各自 LL，不取本字段。
    """

    node_id: str
    site: str
    region: Dn42OriginRegionCommunity = Dn42OriginRegionCommunity.ASIA_EAST
    asn: int = Field(ge=1)
    router_id: str
    ipv4_prefixes: list[str] = Field(default_factory=list)
    ipv6_prefixes: list[str] = Field(default_factory=list)
    loopback_ipv4: str | None = None
    loopback_ipv6: str | None = None
    link_local: str | None = None

    @field_validator("router_id", "loopback_ipv4", "loopback_ipv6")
    @classmethod
    def validate_ip(cls, value: str | None) -> str | None:
        if value is not None:
            validate_ip_address(value)
        return value

    @field_validator("link_local")
    @classmethod
    def validate_link_local(cls, value: str | None) -> str | None:
        if value is not None:
            validate_ipv6_link_local_address(value)
        return value

    @field_validator("ipv4_prefixes")
    @classmethod
    def validate_ipv4_prefixes(cls, value: list[str]) -> list[str]:
        for prefix in value:
            validate_dn42_ipv4_network(prefix)
        return value

    @field_validator("ipv6_prefixes")
    @classmethod
    def validate_ipv6_prefixes(cls, value: list[str]) -> list[str]:
        for prefix in value:
            validate_dn42_ipv6_network(prefix)
        return value

    @model_validator(mode="after")
    def validate_loopbacks_belong_to_node_prefixes(self) -> "NodeSpec":
        if self.loopback_ipv4 and not _address_in_any_prefix(self.loopback_ipv4, self.ipv4_prefixes):
            raise ValueError("loopback_ipv4 must belong to one of ipv4_prefixes")
        if self.loopback_ipv6 and not _address_in_any_prefix(self.loopback_ipv6, self.ipv6_prefixes):
            raise ValueError("loopback_ipv6 must belong to one of ipv6_prefixes")
        return self


def _address_in_any_prefix(address: str, prefixes: list[str]) -> bool:
    return any(is_address_in_prefix(address, prefix) for prefix in prefixes)


class WireGuardPeerSpec(StrictModel):
    """单个 WireGuard 对端的连接参数。

    Attributes:
        public_key: 对端公钥。
        preshared_key_ref: 预共享密钥引用；如何解析由上层运行环境决定。
        endpoint: 对端连接端点，例如 `host:port`。
        allowed_ips: 应写入 peer 的 allowed IP 列表。
        persistent_keepalive_seconds: 持久保活间隔，单位为秒。
    """

    public_key: str
    preshared_key_ref: str | None = None
    endpoint: str | None = None
    allowed_ips: list[str] = Field(default_factory=list)
    persistent_keepalive_seconds: int | None = Field(default=None, ge=1)

    @field_validator("public_key")
    @classmethod
    def validate_public_key(cls, value: str) -> str:
        return validate_wireguard_key(value)

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_wireguard_endpoint(value)

    @field_validator("allowed_ips")
    @classmethod
    def validate_allowed_ips(cls, value: list[str]) -> list[str]:
        for prefix in value:
            validate_ip_network(prefix)
        return value


class InterfaceSpec(StrictModel):
    """节点上的单个网络接口定义。

    当前主要用于描述 dummy loopback 和 WireGuard 接口。

    Attributes:
        name: 接口名；需满足 Linux 长度限制。
        kind: 接口类型，例如 `dummy` 或 `wireguard`。
        mtu: 接口 MTU；为 `None` 时表示由运行时或脚本默认值处理。
        addresses: 要配置到该接口上的地址列表。
        peer_routes: 与该接口对端相关的直连或宿主路由列表。
        listen_port: WireGuard 监听端口。
        private_key_ref: WireGuard 私钥引用。
        wireguard_peer: WireGuard 对端定义；仅在 `kind=wireguard` 时有效。
    """

    name: str
    kind: InterfaceKind
    mtu: int | None = Field(default=1420, ge=576, le=9000)
    addresses: list[str] = Field(default_factory=list)
    peer_routes: list[str] = Field(default_factory=list)
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    private_key_ref: str | None = None
    wireguard_peer: WireGuardPeerSpec | None = None

    @field_validator("addresses")
    @classmethod
    def validate_addresses(cls, value: list[str]) -> list[str]:
        for address in value:
            validate_ip_interface(address)
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if len(value) > 15:
            raise ValueError("interface name must be 15 characters or fewer for Linux compatibility")
        return value

    @field_validator("peer_routes")
    @classmethod
    def validate_routes(cls, value: list[str]) -> list[str]:
        for route in value:
            validate_ip_network(route)
        return value

    @model_validator(mode="after")
    def validate_wireguard_fields(self) -> "InterfaceSpec":
        if self.kind == InterfaceKind.WIREGUARD and not self.private_key_ref:
            raise ValueError("wireguard interfaces require private_key_ref")
        if self.kind == InterfaceKind.WIREGUARD and self.wireguard_peer is None:
            raise ValueError("wireguard interfaces require wireguard_peer")
        if self.kind != InterfaceKind.WIREGUARD and self.wireguard_peer is not None:
            raise ValueError("wireguard_peer is only valid on wireguard interfaces")
        return self
