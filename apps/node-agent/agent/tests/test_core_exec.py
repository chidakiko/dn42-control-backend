from __future__ import annotations

"""``DockerContainerExec``（Docker SDK 容器执行层）的单元测试。

锁定不变量：

* ``run`` 走 Engine API ``exec_run(demux=True)``，stdout/stderr 解码分离，
  None 输出降级为空串；
* ``put_file`` 经 ``put_archive`` 推送 tar：单文件、权限按参数声明、内容逐字节一致；
* client 惰性创建、跨调用复用，``close()`` 释放并允许重建；
* ``container_output_runner`` 区分采集失败（异常 / 非零退出 → ``None``）与
  采集成功（``str``，含空串）。
"""

import io
import tarfile

from agent.core.exec import DockerContainerExec, container_output_runner


class _FakeContainer:
    def __init__(self) -> None:
        self.exec_calls: list[list[str]] = []
        self.archives: list[tuple[str, bytes]] = []
        self.exec_result: tuple[int | None, tuple[bytes | None, bytes | None] | None] = (
            0,
            (b"out", b"err"),
        )

    def exec_run(self, argv: list[str], demux: bool = False):
        assert demux, "必须 demux 分离 stdout/stderr"
        self.exec_calls.append(argv)
        return self.exec_result

    def put_archive(self, path: str, data: bytes) -> None:
        self.archives.append((path, data))


class _FakeContainers:
    def __init__(self, container: _FakeContainer) -> None:
        self._container = container
        self.requested: list[str] = []

    def get(self, name: str) -> _FakeContainer:
        self.requested.append(name)
        return self._container


class _FakeClient:
    def __init__(self, container: _FakeContainer) -> None:
        self.containers = _FakeContainers(container)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _exec_with_fakes() -> tuple[DockerContainerExec, _FakeContainer, list[_FakeClient]]:
    container = _FakeContainer()
    clients: list[_FakeClient] = []

    def factory() -> _FakeClient:
        client = _FakeClient(container)
        clients.append(client)
        return client

    return DockerContainerExec(docker_factory=factory), container, clients


def test_run_decodes_and_demuxes_output() -> None:
    container_exec, container, clients = _exec_with_fakes()

    result = container_exec.run("wg-gw", ["wg", "show", "all", "dump"])

    assert result == (0, "out", "err")
    assert container.exec_calls == [["wg", "show", "all", "dump"]]
    assert clients[0].containers.requested == ["wg-gw"]


def test_run_tolerates_missing_output_streams() -> None:
    container_exec, container, _clients = _exec_with_fakes()
    container.exec_result = (1, (None, None))

    assert container_exec.run("c", ["true"]) == (1, "", "")

    container.exec_result = (None, None)
    returncode, stdout, stderr = container_exec.run("c", ["true"])
    assert (returncode, stdout, stderr) == (-1, "", "")


def test_put_file_ships_single_entry_tar_with_mode() -> None:
    container_exec, container, _clients = _exec_with_fakes()

    container_exec.put_file("wg-gw", "/run/secrets", "node.key", b"PRIVATE", mode=0o600)

    assert len(container.archives) == 1
    dest, payload = container.archives[0]
    assert dest == "/run/secrets"
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        members = archive.getmembers()
        assert [m.name for m in members] == ["node.key"]
        assert members[0].mode == 0o600
        extracted = archive.extractfile(members[0])
        assert extracted is not None
        assert extracted.read() == b"PRIVATE"


def test_put_file_creates_intermediate_directories_in_tar() -> None:
    """filename 含子路径时，中间目录以 0700 目录条目进 tar——不依赖容器内 mkdir。"""

    container_exec, container, _clients = _exec_with_fakes()

    container_exec.put_file("wg-gw", "/run", "dn42-control/secrets/node.key", b"PRIVATE")

    _dest, payload = container.archives[0]
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        members = {m.name: m for m in archive.getmembers()}
        assert set(members) == {"dn42-control", "dn42-control/secrets", "dn42-control/secrets/node.key"}
        assert members["dn42-control"].isdir() and members["dn42-control"].mode == 0o700
        assert members["dn42-control/secrets"].isdir() and members["dn42-control/secrets"].mode == 0o700
        assert members["dn42-control/secrets/node.key"].mode == 0o600


def test_client_is_lazy_reused_and_recreated_after_close() -> None:
    container_exec, _container, clients = _exec_with_fakes()
    assert clients == []  # 惰性：构造时不连 Docker

    container_exec.run("c", ["true"])
    container_exec.run("c", ["true"])
    assert len(clients) == 1  # 跨调用复用

    container_exec.close()
    assert clients[0].closed

    container_exec.run("c", ["true"])
    assert len(clients) == 2  # close 后可重建


def test_container_output_runner_distinguishes_failure_from_empty() -> None:
    container_exec, container, _clients = _exec_with_fakes()

    ok = container_output_runner(container_exec, "c", ["birdc", "show", "protocols"])
    assert ok() == "out"

    # 命令成功但无输出：返回空串（"真的没有"），不是 None。
    container.exec_result = (0, (b"", b""))
    assert ok() == ""

    # 非零退出：采集失败，返回 None（状态未知，下游不当健康）。
    container.exec_result = (1, (b"ignored", b"boom"))
    assert ok() is None

    class _BoomExec:
        def run(self, name: str, argv: list[str]):
            raise OSError("no docker")

        def put_file(self, *args, **kwargs) -> None:
            raise AssertionError("unreachable")

    # 异常：同样视作采集失败。
    assert container_output_runner(_BoomExec(), "c", ["true"])() is None
