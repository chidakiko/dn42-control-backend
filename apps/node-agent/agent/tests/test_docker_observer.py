from __future__ import annotations

"""DockerObserver 的 client 生命周期：跨 reconcile 复用、由持有方统一释放。"""

from dn42_schemas.testing import build_hkg1_example_state

from agent.collectors.docker import DockerObserver


class _FakeContainers:
    def list(self, **kwargs):  # noqa: A003 - 对齐 docker SDK 接口
        return []


class _FakeClient:
    def __init__(self) -> None:
        self.closed = 0
        self.containers = _FakeContainers()

    def close(self) -> None:
        self.closed += 1


def test_docker_observer_reuses_client_and_closes_once() -> None:
    builds: list[_FakeClient] = []

    def factory() -> _FakeClient:
        client = _FakeClient()
        builds.append(client)
        return client

    observer = DockerObserver(docker_factory=factory)
    state = build_hkg1_example_state()

    observer.observe_project(state)
    observer.observe_project(state)
    observer.observe_project(state)

    assert len(builds) == 1  # 跨多次观察只建一次 client（连接池复用）
    assert builds[0].closed == 0  # observe 不再每次 close（旧行为每次新建+关闭）

    observer.close()
    assert builds[0].closed == 1  # 由持有方（Adapters.close）统一释放

    observer.observe_project(state)
    assert len(builds) == 2  # close 后按需重建
