from __future__ import annotations

"""Docker 构建产物（router Dockerfile）的模板渲染入口。

容器的运行参数（网络、端口、挂载、依赖……）不再渲染成任何编排文件——
它们以 `DesiredState.runtime` 的结构化数据从数据库直达 agent 的
Docker Engine API 后端。本模块只负责镜像构建仍需要的文件产物。
"""

from pathlib import Path

from dn42_common import create_environment
from dn42_schemas import RouterDockerfileSpec
from jinja2 import Environment

from .paths import config_docker_template_dir


def create_config_docker_environment(template_dir: str | Path | None = None) -> Environment:
    """创建用于渲染 Docker 构建模板的 Jinja2 环境。"""

    return create_environment(template_dir or config_docker_template_dir())


def render_router_dockerfile(
    dockerfile: RouterDockerfileSpec | None = None,
    env: Environment | None = None,
) -> str:
    """渲染路由器容器使用的 Dockerfile。"""

    active_env = env or create_config_docker_environment()
    active_dockerfile = dockerfile or RouterDockerfileSpec()
    return active_env.get_template("router/Dockerfile.j2").render(
        base_image=active_dockerfile.base_image,
        debian_mirror=active_dockerfile.debian_mirror,
    )


__all__ = [
    "create_config_docker_environment",
    "render_router_dockerfile",
]
