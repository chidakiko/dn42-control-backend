from __future__ import annotations

"""返回包内 runtime 模板目录路径的辅助函数。"""

from importlib.resources import files


def _template_dir(name: str) -> str:
    return str(files("dn42_runtime").joinpath(name))


def config_docker_template_dir() -> str:
    """返回打包后的 Docker 构建模板目录。"""

    return _template_dir("config-docker")
