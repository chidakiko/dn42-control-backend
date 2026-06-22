from __future__ import annotations

"""CoreDNS 配置模板的上下文构造与渲染入口。"""

from pathlib import Path
from typing import Any

from dn42_common import create_environment
from dn42_schemas import DesiredState, DnsZoneSpec
from jinja2 import Environment

from .paths import config_coredns_template_dir


def create_config_coredns_environment(template_dir: str | Path | None = None) -> Environment:
    """创建用于渲染 CoreDNS 模板的 Jinja2 环境。"""

    return create_environment(template_dir or config_coredns_template_dir())


def build_config_coredns_context(state: DesiredState) -> dict[str, Any]:
    """把 DNS 相关 schema 翻译成 CoreDNS 模板上下文。"""

    if not state.dns:
        return {"bind_addresses": [], "cache_ttl_seconds": 300, "zones": [], "forwards": []}
    return {
        "bind_addresses": state.dns.bind_addresses,
        "cache_ttl_seconds": state.dns.cache_ttl_seconds,
        "zones": [
            {
                "zone": zone.zone,
                "zone_file": zone.records_ref.removeprefix("zone://").replace("/", "_"),
            }
            for zone in sorted(state.dns.zones, key=lambda item: item.zone)
        ],
        "forwards": sorted(state.dns.forwards, key=lambda item: item.zone),
    }


def render_corefile(state: DesiredState, env: Environment | None = None) -> str:
    """渲染 CoreDNS Corefile；当 DNS 未启用时返回空字符串。"""

    if not state.dns:
        return ""
    active_env = env or create_config_coredns_environment()
    return active_env.get_template("Corefile.j2").render(**build_config_coredns_context(state))


def zone_file_name(zone: DnsZoneSpec) -> str:
    """由 `records_ref` 推导出与 Corefile 引用一致的 zone 文件名后缀。"""

    return zone.records_ref.removeprefix("zone://").replace("/", "_")


def build_dns_zone_context(zone: DnsZoneSpec, *, serial: int) -> dict[str, Any]:
    """把单个 zone 翻译成 zone 文件模板上下文，`serial` 通常取期望状态的 generation。"""

    origin = zone.zone if zone.zone.endswith(".") else f"{zone.zone}."
    return {
        "origin": origin,
        "default_ttl": zone.default_ttl,
        "primary_ns": zone.primary_ns,
        "admin_email": zone.admin_email,
        "serial": serial,
        "soa_refresh": zone.soa_refresh,
        "soa_retry": zone.soa_retry,
        "soa_expire": zone.soa_expire,
        "soa_minimum": zone.soa_minimum,
        "records": zone.records,
    }


def render_dns_zone(
    zone: DnsZoneSpec, *, serial: int, env: Environment | None = None
) -> str:
    """渲染单个 zone 的 BIND 风格 zone 文件（含 SOA，serial 由调用方给定）。"""

    active_env = env or create_config_coredns_environment()
    return active_env.get_template("zones/db.zone.j2").render(
        **build_dns_zone_context(zone, serial=serial)
    )


__all__ = [
    "build_config_coredns_context",
    "build_dns_zone_context",
    "create_config_coredns_environment",
    "render_corefile",
    "render_dns_zone",
    "zone_file_name",
]
