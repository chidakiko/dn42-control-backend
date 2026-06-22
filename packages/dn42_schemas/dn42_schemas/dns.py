from __future__ import annotations

"""DNS zone / forward / 总体配置 schema。

DN42 上 forward zone 常为单 label（`dn42` / `neo`），因此 zone 字段统一走
`validate_domain_name(require_multi_label=False)`，而不是默认的多 label 规则。
"""

from dn42_common import validate_domain_name, validate_ip_address
from pydantic import Field, field_validator, model_validator

from .base import StrictModel


_DNS_RECORD_TYPES = frozenset(
    {"A", "AAAA", "CNAME", "NS", "PTR", "TXT", "MX", "SRV", "CAA"}
)

# DNS TTL / SOA 计时器 / 缓存 TTL 上限取 PostgreSQL int32（DNS TTL 按 RFC 2181 本就
# ≤ 2147483647）。不设上限时 schema 放行的超大值会在 PG int32 列上溢出（SQLite 动态
# 宽度悄悄存下、PG 报 NumericValueOutOfRange）。
_INT32_MAX = 2_147_483_647


class DnsRecordSpec(StrictModel):
    """zone 文件里的一条资源记录。

    Attributes:
        name: 记录名，可为相对 owner（如 `ns1`）、`@`（zone apex）或 FQDN。
        type: 记录类型，限定于常用集合（A/AAAA/CNAME/NS/PTR/TXT/MX/SRV/CAA）。
        value: 记录值（rdata），按类型由调用方保证语义正确。
        ttl: 可选的单条 TTL，省略时回落到 zone 的 `default_ttl`。
    """

    name: str
    type: str
    value: str
    ttl: int | None = Field(default=None, ge=0, le=_INT32_MAX)

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in _DNS_RECORD_TYPES:
            raise ValueError(f"unsupported DNS record type: {value}")
        return normalized


class DnsZoneSpec(StrictModel):
    """本地接管的 DNS zone。

    `zone` 可为单 label（如 `dn42`），`records_ref` 是外部 zonefile / record
    集的逻辑引用。若 `records` 非空，则由模板层直接渲染出 zone 文件，此时必须
    提供 `primary_ns` 与 `admin_email` 以构造 SOA。

    Attributes:
        zone: zone 名（允许单 label）。
        records_ref: 外部 record 集的逻辑引用（如 `zone://example.dn42`）。
        primary_ns: SOA 主 NS 的 FQDN（如 `ns1.example.dn42.`）；有内联 records 时必填。
        admin_email: SOA 管理邮箱的 zone 文件写法（如 `hostmaster.example.dn42.`）；有内联 records 时必填。
        soa_refresh / soa_retry / soa_expire / soa_minimum: SOA 计时参数（秒）。
        default_ttl: zone 文件 `$TTL`，单条 record 未给 ttl 时使用。
        records: 内联资源记录；非空时触发 zone 文件渲染。
    """

    zone: str
    records_ref: str
    primary_ns: str | None = None
    admin_email: str | None = None
    soa_refresh: int = Field(default=86400, ge=0, le=_INT32_MAX)
    soa_retry: int = Field(default=7200, ge=0, le=_INT32_MAX)
    soa_expire: int = Field(default=3600000, ge=0, le=_INT32_MAX)
    soa_minimum: int = Field(default=3600, ge=0, le=_INT32_MAX)
    default_ttl: int = Field(default=3600, ge=0, le=_INT32_MAX)
    records: list[DnsRecordSpec] = Field(default_factory=list)

    @field_validator("zone")
    @classmethod
    def validate_zone(cls, value: str) -> str:
        # allow_slash：RFC 2317 无类反向委派 zone 名含 `/`（如 0/26.0.20.172.in-addr.arpa）。
        return validate_domain_name(value, require_multi_label=False, allow_slash=True)

    @model_validator(mode="after")
    def validate_soa_when_inlined(self) -> "DnsZoneSpec":
        if self.records and not (self.primary_ns and self.admin_email):
            raise ValueError(
                "inline DNS records require both primary_ns and admin_email for the SOA record"
            )
        return self



class DnsForwardSpec(StrictModel):
    """转发某个 zone 到一组上游 resolver。`upstreams` 逐项走 `validate_ip_address`。"""

    zone: str
    upstreams: list[str]

    @field_validator("zone")
    @classmethod
    def validate_zone(cls, value: str) -> str:
        return validate_domain_name(value, require_multi_label=False)

    @field_validator("upstreams")
    @classmethod
    def validate_upstreams(cls, value: list[str]) -> list[str]:
        for upstream in value:
            validate_ip_address(upstream)
        return value


class DnsSpec(StrictModel):
    """节点本地 DNS 服务的总体配置。

    该项为 `None` 等价于不部署 DNS；`enabled=False` 则保留配置但模板层
    不输出 Corefile（参见 `dn42_templates.coredns.render_corefile`）。
    """

    enabled: bool = True
    bind_addresses: list[str]
    cache_ttl_seconds: int = Field(default=300, ge=0, le=_INT32_MAX)
    zones: list[DnsZoneSpec] = Field(default_factory=list)
    forwards: list[DnsForwardSpec] = Field(default_factory=list)

    @field_validator("bind_addresses")
    @classmethod
    def validate_bind_addresses(cls, value: list[str]) -> list[str]:
        for address in value:
            validate_ip_address(address)
        return value
