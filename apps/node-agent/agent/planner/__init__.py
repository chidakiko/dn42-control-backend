from __future__ import annotations

"""决策层：一次 reconcile 的全部"该改什么"在这里一次成型。

- `reconcile_plan`：聚合入口，产出唯一权威的 `ReconcilePlan`；
- `file_plan`：文件层动作（create/update/delete/noop，含 prune）；
- `container_plan`：容器层动作（内容寻址 + 依赖传播）；
- `convergence_plan`：定向热加载动作（由前两者推导）。

执行层（`agent.apply`）只许照单执行，不允许自行重新决策。
"""

from .container_plan import (
    ContainerAction,
    ContainerPlan,
    ContainerStep,
    build_container_plan,
)
from .definition import (
    ContainerDefinition,
    build_node_definitions,
    diff_payload_keys,
    payload_hash,
)
from .convergence_plan import (
    ConvergenceAction,
    ConvergenceKind,
    ConvergencePlan,
    build_convergence_plan,
)
from .file_plan import build_file_plan_for_state
from .reconcile_plan import ReconcilePlan, build_reconcile_plan


__all__ = [
    "ContainerAction",
    "ContainerDefinition",
    "ContainerPlan",
    "ContainerStep",
    "ConvergenceAction",
    "ConvergenceKind",
    "ConvergencePlan",
    "ReconcilePlan",
    "build_container_plan",
    "build_convergence_plan",
    "build_file_plan_for_state",
    "build_node_definitions",
    "build_reconcile_plan",
    "diff_payload_keys",
    "payload_hash",
]
