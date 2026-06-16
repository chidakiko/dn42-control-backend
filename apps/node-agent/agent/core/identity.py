from __future__ import annotations

"""持久化 agent 注册凭据与最近世代信息。"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from dn42_common import atomic_write_json


@dataclass(slots=True)
class LocalAgentIdentity:
    """节点 agent 的本地身份与已应用世代。

    Attributes:
        node_id: Control Server 分配的节点 ID。
        agent_id: Control Server 分配的 agent 实例 ID。
        agent_token: 调用 Agent API 使用的 bearer token。
        applied_generation: 上次成功 apply 的 desired-state generation。
        last_apply_status: 上次 apply 的状态字符串（succeeded/degraded/...）。
        last_apply_at: 上次 apply 的 ISO 8601 时间。
    """

    node_id: str | None = None
    agent_id: str | None = None
    agent_token: str | None = None
    applied_generation: int | None = None
    last_apply_status: str | None = None
    last_apply_at: str | None = None


def load_identity(path: Path) -> LocalAgentIdentity:
    """从磁盘加载身份信息；文件不存在时返回空身份。"""

    if not path.exists():
        return LocalAgentIdentity()
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return LocalAgentIdentity(**payload)


def save_identity(identity: LocalAgentIdentity, path: Path) -> None:
    """把身份信息原子地写入磁盘。"""

    atomic_write_json(path, asdict(identity))


__all__ = ["LocalAgentIdentity", "load_identity", "save_identity"]
