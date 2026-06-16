from __future__ import annotations

"""`bird-lg-go` / `bird-lgproxy-go` looking-glass 配置。

仅描述“希望怎么跑 looking glass”本身；Router runtime 服务调整由
`DesiredState.validate_references` 中的 `_normalize_lookglass_runtime`
自动完成（注入 proxy/frontend service 与 bird-router 的共享 socket 挂载）。
因此调用方**不应**手工在 `runtime.services` 里贴 lookglass 服务。
"""

from dn42_common import validate_domain_name
from pydantic import Field, field_validator

from .base import StrictModel


class LookglassSpec(StrictModel):
    """节点 looking-glass 側车配置（`bird-lg-go` / `bird-lgproxy-go`）。

    主要控制 4 件事：

    1. 是否开启 / 是否连同同 frontend 部署；
    2. proxy / frontend 服务名与镜像；
    3. 与 bird-router 共享 control socket 的卷 (`shared_socket_dir`)；
    4. frontend 的外联发布、访问控制与品牌文案。

    全部 `runtime` 側的调整由 `DesiredState.validate_references` 自动完成，
    调用方不应手工在 `RouterRuntimeSpec.services` 里插入 lookglass 服务。
    """

    enabled: bool = True
    frontend_enabled: bool = False
    proxy_service_name: str = "dn42-bird-lg-proxy"
    frontend_service_name: str = "dn42-bird-lg"
    proxy_image: str = "xddxdd/bird-lgproxy-go:latest"
    frontend_image: str = "xddxdd/bird-lg-go:latest"
    shared_socket_dir: str = "runtime/bird-run"
    proxy_port: int = Field(default=8000, ge=1, le=65535)
    frontend_port: int = Field(default=5000, ge=1, le=65535)
    published_frontend_ports: list[str] = Field(default_factory=list)
    servers: list[str] = Field(default_factory=list)
    domain: str = ""
    allowed_ips: list[str] = Field(default_factory=list)
    title_brand: str = "DN42 looking glass"
    navbar_brand: str | None = None
    protocol_filter: list[str] = Field(default_factory=lambda: ["Babel", "BGP", "OSPF"])
    whois_command: str = "/usr/bin/whois"
    net_specific_mode: str = "dn42"

    @field_validator(
        "proxy_service_name",
        "frontend_service_name",
        "proxy_image",
        "frontend_image",
        "shared_socket_dir",
        "title_brand",
        "whois_command",
        "net_specific_mode",
    )
    @classmethod
    def validate_string_fields(cls, value: str) -> str:
        return value.strip()

    @field_validator("published_frontend_ports", "servers", "allowed_ips", "protocol_filter")
    @classmethod
    def validate_string_lists(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("lookglass list entries must be unique")
        return normalized

    @field_validator("domain")
    @classmethod
    def validate_domain_field(cls, value: str) -> str:
        candidate = value.strip()
        if not candidate:
            return ""
        return validate_domain_name(candidate)
