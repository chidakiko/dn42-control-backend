from __future__ import annotations

"""WireGuard 配置模板的上下文构造与渲染入口。"""

from pathlib import Path
from typing import Any

from dn42_common import create_environment
from dn42_schemas import InterfaceSpec
from jinja2 import Environment

from .paths import config_wireguard_template_dir


def create_config_wireguard_environment(template_dir: str | Path | None = None) -> Environment:
    """创建用于渲染 WireGuard 模板的 Jinja2 环境。"""

    return create_environment(template_dir or config_wireguard_template_dir())


def build_config_wireguard_context(interface: InterfaceSpec) -> dict[str, Any]:
    """把 `InterfaceSpec` 翻译成 WireGuard 模板使用的上下文字典。"""

    return {
        "name": interface.name,
        "private_key_ref": interface.private_key_ref,
        "listen_port": interface.listen_port,
        "mtu": interface.mtu,
        "peer": interface.wireguard_peer,
    }


def render_wireguard(interface: InterfaceSpec, env: Environment | None = None) -> str:
    """渲染单个 WireGuard 接口配置文件内容。"""

    active_env = env or create_config_wireguard_environment()
    return active_env.get_template("interface.conf.j2").render(
        **build_config_wireguard_context(interface)
    )


__all__ = [
    "build_config_wireguard_context",
    "create_config_wireguard_environment",
    "render_wireguard",
]
