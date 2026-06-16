from __future__ import annotations

"""控制面 → Agent 推送的 WS 事件类型。

WS 仅承担"事件门铃"：业务数据走 HTTP；这里枚举可能下发的事件 schema，
供 control-server 与 node-agent 共享一套字面量。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HelloEvent(_Event):
    """连接握手成功后立即下发，告诉 agent 当前应该处于哪个世代。"""

    type: Literal["hello"] = "hello"
    node_id: str
    generation: int | None = None


class DesiredStateUpdatedEvent(_Event):
    """通知 agent 控制面已发布新世代，agent 应通过 HTTP 拉取。

    `reason` 是控制面对"这次为什么变"的可读描述（如 `interface updated`），
    仅供日志与排错；agent 的收敛正确性不依赖它——实际差异由 agent 本地
    对比渲染产物与运行态得出。
    """

    type: Literal["desired_state_updated"] = "desired_state_updated"
    generation: int = Field(ge=1)
    reason: str | None = None


class SnapshotRequestEvent(_Event):
    """要求 agent 主动上报一份最新 RuntimeSnapshot（仍走 HTTP POST）。"""

    type: Literal["snapshot_request"] = "snapshot_request"
    reason: str | None = None


__all__ = [
    "DesiredStateUpdatedEvent",
    "HelloEvent",
    "SnapshotRequestEvent",
]
