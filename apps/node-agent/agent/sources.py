from __future__ import annotations

"""DesiredState 来源协议与三种实现。

reconcile 管线不关心状态从哪来，只跟 `DesiredStateSource` 说话：

- `ControllerSource`：经 Session（含注册与 401 自愈）从控制面拉取；
- `LocalFileSource`：离线诊断，从 JSON 文件加载；
- `BuiltinExampleSource`：零依赖自检，使用内置 hkg1 示例。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dn42_schemas import DesiredState
from dn42_schemas.testing import build_hkg1_example_state

from .core.config import AgentConfig
from .desired_state.loader import load_desired_state_from_file
from .session import Session


class DesiredStateSource(Protocol):
    """统一的状态来源接口。``name`` 进入结果摘要的 ``source`` 字段。"""

    @property
    def name(self) -> str:
        ...

    def fetch(self) -> DesiredState:
        ...


@dataclass(frozen=True, slots=True)
class ControllerSource:
    session: Session
    name: str = "controller"

    def fetch(self) -> DesiredState:
        return self.session.call(lambda client: client.fetch_desired_state())


@dataclass(frozen=True, slots=True)
class LocalFileSource:
    path: Path
    name: str = "local-file"

    def fetch(self) -> DesiredState:
        return load_desired_state_from_file(self.path)


@dataclass(frozen=True, slots=True)
class BuiltinExampleSource:
    name: str = "built-in-example"

    def fetch(self) -> DesiredState:
        return build_hkg1_example_state()


def select_source(config: AgentConfig, session: Session | None) -> DesiredStateSource:
    """按配置选择状态来源（controller 与本地文件互斥已在 CLI 层校验）。"""

    if config.controller_url is not None:
        assert session is not None, "controller 模式必须有 Session"
        return ControllerSource(session=session)
    if config.desired_state_path is not None:
        return LocalFileSource(path=config.desired_state_path)
    return BuiltinExampleSource()


__all__ = [
    "BuiltinExampleSource",
    "ControllerSource",
    "DesiredStateSource",
    "LocalFileSource",
    "select_source",
]
