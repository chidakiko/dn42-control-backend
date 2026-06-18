from __future__ import annotations

"""dn42_templates 的公开模板渲染 API。

这个包负责把结构化的 schema 输入翻译成实际部署文件，主要覆盖：

- BIRD2 配置
- WireGuard 接口配置
- 节点启动脚本
- CoreDNS 配置
- 基于 `DesiredState` 的整节点文件集渲染

大多数调用方只需要直接使用 `render_desired_state()`，
只有在需要单独测试或覆写某一类模板输出时，才会调用子模块里的细粒度渲染函数。
"""

from .bird2 import (
    Bird2RenderedFile,
    CONFIG_BIRD2_TEMPLATE_NAMES,
    bird_protocol_name,
    build_config_bird2_context,
    create_config_bird2_environment,
    render_config_bird2_set,
    render_config_bird2_template,
)
from .coredns import (
    build_config_coredns_context,
    build_dns_zone_context,
    create_config_coredns_environment,
    render_corefile,
    render_dns_zone,
    zone_file_name,
)
from .desired_state import render_bird_conf, render_desired_state
from .paths import (
    config_bird2_template_dir,
    config_coredns_template_dir,
    config_scripts_template_dir,
    config_wireguard_template_dir,
)
from .scripts import (
    build_config_scripts_context,
    create_config_scripts_environment,
    render_apply_all_wg_script,
    render_bird_apply_script,
    render_bird_start_script,
    render_loopback_script,
    render_wireguard_apply_script,
    render_wireguard_start_script,
)
from .wireguard import (
    build_config_wireguard_context,
    create_config_wireguard_environment,
    render_wireguard,
)


__all__ = [
    "Bird2RenderedFile",
    "CONFIG_BIRD2_TEMPLATE_NAMES",
    "bird_protocol_name",
    "build_config_bird2_context",
    "build_config_coredns_context",
    "build_config_scripts_context",
    "build_config_wireguard_context",
    "build_dns_zone_context",
    "config_bird2_template_dir",
    "config_coredns_template_dir",
    "config_scripts_template_dir",
    "config_wireguard_template_dir",
    "create_config_bird2_environment",
    "create_config_coredns_environment",
    "create_config_scripts_environment",
    "create_config_wireguard_environment",
    "render_apply_all_wg_script",
    "render_bird_apply_script",
    "render_bird_conf",
    "render_bird_start_script",
    "render_config_bird2_set",
    "render_config_bird2_template",
    "render_corefile",
    "render_desired_state",
    "render_dns_zone",
    "render_loopback_script",
    "render_wireguard",
    "render_wireguard_apply_script",
    "render_wireguard_start_script",
    "zone_file_name",
]
