from __future__ import annotations

"""容器内执行的统一注入点（Docker Engine API，不依赖 docker CLI）。

agent 里所有需要进入受管容器的副作用——执行命令（birdc / wg / sh）与推送
文件——都通过 ``ContainerExec`` 形态接收执行器：生产用 ``DockerContainerExec``
（Docker SDK，exec_run / put_archive，只需要 socket，不需要 docker 二进制），
单测注入假实现，完全不碰真实 Docker。
"""

import importlib
import io
import tarfile
from collections.abc import Callable
from typing import Any, Protocol

# (returncode, stdout, stderr)
ExecResult = tuple[int, str, str]


class ContainerExec(Protocol):
    """容器内副作用边界：exec + 文件推送。"""

    def run(self, container: str, argv: list[str]) -> ExecResult:
        """在容器内执行 argv，返回 (returncode, stdout, stderr)。"""
        ...

    def put_file(
        self, container: str, dest_dir: str, filename: str, data: bytes, *, mode: int = 0o600
    ) -> None:
        """把 data 以 ``dest_dir/filename`` 写进容器。

        ``dest_dir`` 必须已存在；``filename`` 可含相对子路径，缺失的中间
        目录以 0700 一并创建。实现不依赖容器处于 running 状态（文件系统
        操作对 created/restarting 容器同样生效）。
        """
        ...


class DockerContainerExec:
    """生产实现：经 Docker SDK 直连 Engine API。

    client 惰性创建并跨 reconcile 复用（unix socket 连接池），由持有方
    （``Adapters.close()``）统一释放。
    """

    def __init__(self, docker_factory: Callable[[], Any] | None = None) -> None:
        self._docker_factory = docker_factory
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            if self._docker_factory is not None:
                self._client = self._docker_factory()
            else:
                docker_sdk = importlib.import_module("docker")
                self._client = docker_sdk.from_env()
        return self._client

    def run(self, container: str, argv: list[str]) -> ExecResult:
        target = self._get_client().containers.get(container)
        returncode, output = target.exec_run(argv, demux=True)
        stdout, stderr = output if output is not None else (None, None)
        return (
            returncode if returncode is not None else -1,
            (stdout or b"").decode("utf-8", errors="replace"),
            (stderr or b"").decode("utf-8", errors="replace"),
        )

    def put_file(
        self, container: str, dest_dir: str, filename: str, data: bytes, *, mode: int = 0o600
    ) -> None:
        target = self._get_client().containers.get(container)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            # filename 的中间目录作为 0700 目录条目进 tar，免去对容器内
            # mkdir exec 的依赖（exec 要求容器 running，put_archive 不要求）。
            parts = filename.replace("\\", "/").split("/")
            for depth in range(1, len(parts)):
                directory = tarfile.TarInfo(name="/".join(parts[:depth]))
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o700
                archive.addfile(directory)
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            info.mode = mode
            archive.addfile(info, io.BytesIO(data))
        target.put_archive(dest_dir, buffer.getvalue())

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - 释放连接是 best-effort
            pass
        self._client = None


def container_output_runner(
    container_exec: ContainerExec, container: str, argv: list[str]
) -> Callable[[], str | None]:
    """把「在容器内执行 argv」包装成 ``() -> str | None`` 的只读采集器入口。

    返回值区分**采集失败**与**采集成功但无输出**：

    - ``None``：docker 不可用 / 容器不存在 / 命令非零退出——采集失败，状态未知；
    - ``str``（可能为空串）：命令成功退出，输出权威（空串即"真的没有"）。

    下游据此把"采集失败"标为 unavailable（不当健康），把"成功但空"按真实
    缺失判 drift，不再让采集失败静默冒充无 drift。
    """

    def call() -> str | None:
        try:
            returncode, stdout, _stderr = container_exec.run(container, argv)
        except Exception:  # noqa: BLE001 - 观察是 best-effort
            return None
        return stdout if returncode == 0 else None

    return call


__all__ = ["ContainerExec", "DockerContainerExec", "ExecResult", "container_output_runner"]
