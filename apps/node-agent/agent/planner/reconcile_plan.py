from __future__ import annotations

"""ReconcilePlan：一次 reconcile 的**唯一权威决策产物**。

决策层（planner）在观测完成后一次性产出完整计划——文件动作（含 prune）、
容器动作（含依赖传播）、收敛动作（由前两者推导）。此后：

- 执行层（agent.apply）**严格照单执行**，不允许自行重新决策；
- 上报层从同一份计划 + 执行结果派生 `ApplyResult` / `PlanSummary`；
- ``--plan-only`` 展示的就是这份计划本身。

由构造保证"计划说什么、执行做什么、上报报什么"三者一致。
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dn42_runtime import FilePlan, build_file_plan
from dn42_schemas import DesiredState, ObservedContainer

from ..render.pipeline import RenderedBundle
from .container_plan import ContainerPlan, build_container_plan
from .convergence_plan import ConvergencePlan, build_convergence_plan


@dataclass(frozen=True, slots=True)
class ReconcilePlan:
    """文件 / 容器 / 收敛三层动作的聚合计划。"""

    file_plan: FilePlan
    container_plan: ContainerPlan
    convergence_plan: ConvergencePlan


def build_reconcile_plan(
    state: DesiredState,
    bundle: RenderedBundle,
    rendered_dir: Path,
    observed_containers: list[ObservedContainer],
    *,
    previous_definitions: Mapping[str, dict[str, Any]] | None = None,
) -> ReconcilePlan:
    """从渲染产物与观测结果一次性产出完整计划。

    文件计划始终开 ``prune``：被删除资源的孤儿文件列为 ``delete``，
    与执行端真实行为一致（修复"上报无 delete、磁盘却删了"的审计失真）。
    """

    file_plan = build_file_plan(
        bundle.files,
        rendered_dir if rendered_dir.exists() else None,
        prune=True,
    )
    container_plan = build_container_plan(
        state,
        observed_containers,
        rendered_dir=rendered_dir,
        previous_definitions=previous_definitions,
    )
    convergence_plan = build_convergence_plan(state, container_plan, file_plan.actions)
    return ReconcilePlan(
        file_plan=file_plan,
        container_plan=container_plan,
        convergence_plan=convergence_plan,
    )


__all__ = ["ReconcilePlan", "build_reconcile_plan"]
