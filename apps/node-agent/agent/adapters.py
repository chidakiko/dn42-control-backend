from __future__ import annotations

"""Adapters：agent 全部副作用边界的单一装配点。

reconcile 管线只接收一个 `Adapters` 对象，不再逐个传递注入参数。生产装配
用 `Adapters.build(config)`（缺什么补什么默认实现）；测试装配同一入口、
传入假实现即可：

```python
adapters = Adapters.build(
    config,
    controller=mock_client,
    docker_observer=FakeObserver(),
    container_exec=FakeContainerExec(),
)
```

生命周期：守护进程启动时装配一次并持有（HTTP 连接池与 Docker client 跨
reconcile 复用），退出时 `close()`；单次模式由 `run_once` 自建自关。
"""

from dataclasses import dataclass

from .apply.executor import ApplyExecutor
from .client.controller import ControllerClient
from .collectors.docker import DockerObserver
from .collectors.inventory import build_host_inventory
from .core.config import AgentConfig
from .core.exec import ContainerExec, DockerContainerExec
from .session import InventoryBuilder, Session


@dataclass
class Adapters:
    """副作用边界集合；构造请用 `Adapters.build`。"""

    controller: ControllerClient | None
    session: Session | None
    docker_observer: DockerObserver
    apply_executor: ApplyExecutor
    container_exec: ContainerExec

    @classmethod
    def build(
        cls,
        config: AgentConfig,
        *,
        controller: ControllerClient | None = None,
        docker_observer: DockerObserver | None = None,
        apply_executor: ApplyExecutor | None = None,
        container_exec: ContainerExec | None = None,
        inventory_builder: InventoryBuilder = build_host_inventory,
    ) -> "Adapters":
        """按配置装配；未显式注入的部件使用生产默认实现。"""

        if controller is None and config.controller_url is not None:
            controller = ControllerClient.for_url(
                config.controller_url, timeout=config.http_timeout_seconds
            )
        session = (
            Session(config, controller, inventory_builder=inventory_builder)
            if controller is not None
            else None
        )
        return cls(
            controller=controller,
            session=session,
            docker_observer=docker_observer or DockerObserver(),
            apply_executor=apply_executor or ApplyExecutor(),
            container_exec=container_exec or DockerContainerExec(),
        )

    def close(self) -> None:
        """释放长生命周期资源（HTTP 连接池、Docker client）。"""

        if self.controller is not None:
            self.controller.close()
        closer = getattr(self.container_exec, "close", None)
        if callable(closer):
            closer()


__all__ = ["Adapters"]
