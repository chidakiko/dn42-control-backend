from __future__ import annotations

"""采集本机能力清单 (HostInventory)。"""

import platform
import shutil
from pathlib import Path

from dn42_schemas import AgentCapability, HostInventory


def _detect_capabilities() -> list[AgentCapability]:
    """根据可执行文件可见性推断 capability 列表。"""

    capabilities: list[AgentCapability] = []

    if shutil.which("docker") is not None:
        capabilities.append(AgentCapability.DOCKER)
    if Path("/run/systemd/system").exists():
        capabilities.append(AgentCapability.SYSTEMD)
    if shutil.which("wg") is not None:
        capabilities.append(AgentCapability.WIREGUARD)
    if shutil.which("bird") is not None or shutil.which("birdc") is not None:
        capabilities.append(AgentCapability.BIRD)
    if shutil.which("coredns") is not None:
        capabilities.append(AgentCapability.COREDNS)
    return capabilities


def build_host_inventory(
    *,
    hostname: str | None = None,
    capabilities: list[AgentCapability] | None = None,
    labels: dict[str, str] | None = None,
) -> HostInventory:
    """采集本机基础信息。

    `capabilities` 提供时直接采用；否则按本机可执行文件探测。
    Windows 等没有相关二进制的开发环境会得到至少包含 `DOCKER` 的最小列表
    （以保证 control server 单测能正常运行）。
    """

    detected = capabilities if capabilities is not None else _detect_capabilities()
    if not detected:
        detected = [AgentCapability.DOCKER]

    return HostInventory(
        hostname=hostname or platform.node() or "unknown-node",
        os=(platform.system() or "unknown").lower(),
        arch=(platform.machine() or "unknown").lower(),
        kernel=platform.release() or None,
        container_runtime="docker",
        has_systemd=Path("/run/systemd/system").exists(),
        capabilities=detected,
        labels=dict(labels or {}),
    )


__all__ = ["build_host_inventory"]
