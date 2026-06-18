from __future__ import annotations

"""执行渲染写盘与容器部署的 apply 层。"""

from .convergence import ConvergenceResult, ConvergenceStep, execute_convergence_plan
from .definition_store import load_container_definitions, persist_container_definitions
from .docker_api import DockerApiBackend, build_create_kwargs, topologically_sorted_services
from .executor import ApplyExecutor, DeployResult
from .writer import write_rendered_bundle


__all__ = [
    "ApplyExecutor",
    "ConvergenceResult",
    "ConvergenceStep",
    "DeployResult",
    "DockerApiBackend",
    "build_create_kwargs",
    "execute_convergence_plan",
    "load_container_definitions",
    "persist_container_definitions",
    "topologically_sorted_services",
    "write_rendered_bundle",
]
