from __future__ import annotations

"""apply 执行器：把容器计划交给 Docker API 后端，统一返回类型。

历史上这里在 docker-api 与 docker-compose-cli 两个后端间路由；compose CLI
后端已移除（它无法消费逐容器计划，且是 agent 对 docker 二进制的唯一部署
依赖），现在部署只有一条路径：``DockerApiBackend`` 严格照 ``container_plan``
执行。保留这一层是为了给 reconcile 管线一个可注入的部署边界。
"""

from dataclasses import dataclass
from typing import Any

from dn42_schemas import DesiredState

from ..planner.container_plan import ContainerPlan
from .docker_api import DockerApiBackend


@dataclass(frozen=True, slots=True)
class DeployResult:
    """部署结果的统一包装。"""

    backend: str
    succeeded: bool
    raw: dict[str, Any]


class ApplyExecutor:
    """reconcile 管线的部署边界；测试注入假实现即可绕开真实 Docker。"""

    def __init__(self, *, docker_api: DockerApiBackend | None = None) -> None:
        self._docker_api = docker_api or DockerApiBackend()

    def deploy(
        self,
        *,
        state: DesiredState,
        container_plan: ContainerPlan,
    ) -> DeployResult:
        """严格按 ``container_plan`` 把容器收敛到期望态。"""

        result = self._docker_api.deploy(state, container_plan)
        return DeployResult(backend="docker-api", succeeded=result.succeeded, raw=result.to_dict())


__all__ = ["ApplyExecutor", "DeployResult"]
