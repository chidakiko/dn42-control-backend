from __future__ import annotations

"""DN42 容器/网络 label 的单一来源。

所有由 agent 部署的资源都使用这套 label：

| key | value | 用途 |
| --- | --- | --- |
| `dn42.managed` | `"true"` | 标识该资源由 dn42 控制平面托管。 |
| `dn42.node_id` | `<node_id>` | 反查归属节点；observe 阶段过滤本节点资源。 |
| `dn42.config_hash` | `<sha256[:16]>` | 容器的**内容寻址身份**的存储位置。 |
| `dn42.component` | `<service.role.value>` | 反查角色；用于 ReconciliationReport 分类。 |

`dn42.config_hash` 只是身份的**存储位置**（随容器走，agent 无状态、重装
不触发重建）。哈希的**计算**在 agent 决策层：输入是解析后的容器定义
payload（即将发给 Engine API 的最终参数集），不是 schema 序列化——见
`agent.planner.definition`。schema 重构只要不改变最终 payload 就不会
触发任何重建。

任何包都应通过这里的常量与构造函数读写 label，不允许直接拼字符串。
"""


LABEL_MANAGED = "dn42.managed"
LABEL_NODE_ID = "dn42.node_id"
LABEL_CONFIG_HASH = "dn42.config_hash"
LABEL_COMPONENT = "dn42.component"

LABEL_MANAGED_VALUE = "true"


def network_labels() -> dict[str, str]:
    """underlay / 共享网络只需要 managed 标识。"""

    return {LABEL_MANAGED: LABEL_MANAGED_VALUE}


def container_labels(node_id: str, component: str, config_hash: str) -> dict[str, str]:
    """构造容器的 4 个标准 label。``config_hash`` 由调用方（决策层）计算。"""

    return {
        LABEL_MANAGED: LABEL_MANAGED_VALUE,
        LABEL_NODE_ID: node_id,
        LABEL_CONFIG_HASH: config_hash,
        LABEL_COMPONENT: component,
    }


def node_id_filter(node_id: str) -> list[str]:
    """docker SDK `filters={"label": [...]}` 用的过滤值。"""

    return [f"{LABEL_NODE_ID}={node_id}"]


__all__ = [
    "LABEL_COMPONENT",
    "LABEL_CONFIG_HASH",
    "LABEL_MANAGED",
    "LABEL_MANAGED_VALUE",
    "LABEL_NODE_ID",
    "container_labels",
    "network_labels",
    "node_id_filter",
]
