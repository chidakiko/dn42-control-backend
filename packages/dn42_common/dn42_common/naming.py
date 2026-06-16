from __future__ import annotations

"""命名规则的单一实现。

`dn42_common` 是基础包中的叶子，所有跨 schema/templates/runtime/agent 共享的
命名/标识符规则都集中在这里，避免上层包出现"和某某保持一致"式的隐式耦合。
"""

import re


_NORMALIZE_PATTERN = re.compile(r"[^a-zA-Z0-9_-]+")


def normalize_identifier(value: str) -> str:
    """把任意标识符规范化成 docker / linux 接口名兼容的小写短横风格。

    - 非 `[A-Za-z0-9_-]` 的连续字符 → 单个 `-`；
    - 首尾的 `-` 去掉；
    - 整体小写。
    """

    return _NORMALIZE_PATTERN.sub("-", value).strip("-").lower()


def node_project_name(node_id: str, *, override: str | None = None) -> str:
    """为指定 node_id 派生节点 runtime 项目名（容器/网络名前缀）。

    - 若 `override` 非空（来自 `RouterRuntimeSpec.project_name`），直接使用；
    - 否则 `dn42-<normalized-node-id>`。
    """

    if override:
        return override
    return f"dn42-{normalize_identifier(node_id)}"


def service_container_name(project_name: str, service_name: str) -> str:
    """派生容器名 `<project>-<service>-1`。

    `-1` 后缀是历史命名约定（与早期 compose 部署的容器名兼容），改动会让
    存量节点上的所有容器被视作缺失而整体重建，禁止变更。
    """

    return f"{project_name}-{normalize_identifier(service_name)}-1"


def agent_id_for(node_id: str) -> str:
    """从 node_id 派生 agent 实例 ID `<node_id>-agent`。

    控制面注册响应用它标识 agent 实例;集中一处避免控制面多处字面量拼接、
    将来 agent 侧若需自验时出现第二实现。
    """

    return f"{node_id}-agent"


__all__ = [
    "agent_id_for",
    "node_project_name",
    "normalize_identifier",
    "service_container_name",
]
