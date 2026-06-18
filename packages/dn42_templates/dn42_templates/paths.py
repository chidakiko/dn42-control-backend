from __future__ import annotations

"""返回包内模板目录路径的辅助函数。"""

from importlib.resources import files


def _template_dir(name: str) -> str:
    return str(files("dn42_templates").joinpath(name))


def config_bird2_template_dir() -> str:
    """Return the packaged config-bird2 template directory."""

    return _template_dir("config-bird2")


def config_wireguard_template_dir() -> str:
    """Return the packaged WireGuard template directory."""

    return _template_dir("config-wireguard")


def config_scripts_template_dir() -> str:
    """Return the packaged node script template directory."""

    return _template_dir("config-scripts")


def config_coredns_template_dir() -> str:
    """Return the packaged CoreDNS template directory."""

    return _template_dir("config-coredns")
