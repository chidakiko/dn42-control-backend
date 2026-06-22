from __future__ import annotations

"""Docker 侧观察工具。

抽出独立模块的目的：
- 集中真正调用 Docker SDK 的位置，便于在测试里 monkeypatch；
- 承担 ServiceRole / config_hash 的解析，给 reconcile 提供结构化输入。
"""

from dataclasses import dataclass, field
from typing import Any

from dn42_common import (
    LABEL_COMPONENT,
    LABEL_CONFIG_HASH,
    node_id_filter,
)
from dn42_schemas import (
    DesiredState,
    ObservedContainer,
    RuntimeResourceStatus,
    ServiceRole,
)

from ..core.naming import node_project_name


@dataclass(frozen=True, slots=True)
class ObservedProject:
    """单个节点 runtime 项目当前观察到的容器集合。"""

    project_name: str
    containers: list[ObservedContainer] = field(default_factory=list)


_RUNNING_STATUSES = {"running", "healthy"}
_DEGRADED_STATUSES = {"restarting", "paused", "removing"}


def _parse_status(state: dict[str, Any]) -> RuntimeResourceStatus:
    raw = (state.get("Status") or "").lower()
    if raw in _RUNNING_STATUSES:
        return RuntimeResourceStatus.RUNNING
    if raw in {"exited", "dead", "created"}:
        return RuntimeResourceStatus.STOPPED
    if raw in _DEGRADED_STATUSES:
        return RuntimeResourceStatus.DEGRADED
    return RuntimeResourceStatus.UNKNOWN


def _parse_role(value: str | None) -> ServiceRole | None:
    if value is None:
        return None
    try:
        return ServiceRole(value)
    except ValueError:
        return None


def _parse_health(state: dict[str, Any]) -> bool | None:
    health = state.get("Health")
    if not health:
        return None
    status = (health.get("Status") or "").lower()
    if status == "healthy":
        return True
    if status in {"unhealthy", "starting"}:
        return False
    return None


class DockerObserver:
    """对 docker engine 进行只读观察。

    通过 `docker_factory` 注入，在测试中可以传入返回 stub 的 callable。
    在实际环境中默认调用 `docker.from_env()`。

    client 惰性创建并跨 reconcile 复用（unix socket 连接池），由持有方
    （``Adapters.close()``）统一释放——与 :class:`DockerContainerExec` 对齐，
    避免每轮 reconcile（observe 前后各一次）反复 ``from_env()`` 建/拆连接。
    """

    def __init__(self, docker_factory: Any | None = None) -> None:
        self._docker_factory = docker_factory
        self._client_cache: Any | None = None

    def _client(self) -> Any:
        if self._client_cache is None:
            if self._docker_factory is not None:
                self._client_cache = self._docker_factory()
            else:
                import docker  # 延迟导入避免在没装 SDK 的环境直接失败

                self._client_cache = docker.from_env()
        return self._client_cache

    def close(self) -> None:
        if self._client_cache is None:
            return
        try:
            self._client_cache.close()
        except Exception:  # noqa: BLE001 - 释放连接是 best-effort
            pass
        self._client_cache = None

    def observe_project(self, state: DesiredState) -> ObservedProject:
        """采集本节点 runtime 项目中带 dn42 label 的容器。

        当 Docker 不可用（未装 SDK / 无 socket，例如 write-rendered 模式下的
        agent 容器）时，整体降级为「空观察」而非抛错——观察是只读旁路，不应
        阻断渲染/上报主流程。
        """

        project = node_project_name(state)
        try:
            client = self._client()
            containers = client.containers.list(
                all=True,
                filters={"label": node_id_filter(state.node.node_id)},
            )
        except Exception:
            return ObservedProject(project_name=project)

        observed: list[ObservedContainer] = []
        for container in containers:
            attrs = getattr(container, "attrs", {}) or {}
            labels = attrs.get("Config", {}).get("Labels") or {}
            container_state = attrs.get("State", {}) or {}
            observed.append(
                ObservedContainer(
                    name=attrs.get("Name", "").lstrip("/") or container.name,
                    role=_parse_role(labels.get(LABEL_COMPONENT)),
                    image=attrs.get("Config", {}).get("Image"),
                    config_hash=labels.get(LABEL_CONFIG_HASH),
                    status=_parse_status(container_state),
                    healthy=_parse_health(container_state),
                )
            )
        return ObservedProject(project_name=project, containers=observed)


__all__ = ["DockerObserver", "ObservedProject"]
