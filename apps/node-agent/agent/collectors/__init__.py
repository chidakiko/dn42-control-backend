from __future__ import annotations

"""节点观察事实采集层。

模块职责严格只读：永远不修改宿主或容器状态。
"""

from .docker import DockerObserver, ObservedProject
from .inventory import build_host_inventory
from .network import BgpObserver, WireGuardObserver
from .snapshot import build_runtime_snapshot


__all__ = [
    "BgpObserver",
    "DockerObserver",
    "ObservedProject",
    "WireGuardObserver",
    "build_host_inventory",
    "build_runtime_snapshot",
]
