from __future__ import annotations

"""Agent 命名工具薄封装。

实际规则在 `dn42_common.naming`，这里只提供"接受 DesiredState 的便捷形式"，
保证整个仓库的项目名/容器名规则只有一份事实来源。
"""

from dn42_common import (
    node_project_name as _node_project_name,
    service_container_name as _service_container_name,
)
from dn42_schemas import DesiredState, ServiceRole


def node_project_name(state: DesiredState) -> str:
    """返回该 DesiredState 对应的 runtime 项目名（容器/网络名前缀）。"""

    return _node_project_name(state.node.node_id, override=state.runtime.project_name)


def service_container_name(project_name: str, service_name: str) -> str:
    """推导服务对应的容器名。"""

    return _service_container_name(project_name, service_name)


def service_container_by_role(state: DesiredState, role: ServiceRole) -> str | None:
    """返回指定 role 的 enabled 服务对应的容器名；不存在时返回 None。"""

    project = node_project_name(state)
    for service in state.runtime.services:
        if service.enabled and service.role == role:
            return service_container_name(project, service.name)
    return None


__all__ = ["node_project_name", "service_container_by_role", "service_container_name"]
