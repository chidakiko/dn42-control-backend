from __future__ import annotations

"""根据 desired runtime 与 observed 对比，得到容器层 apply 计划。

容器层 plan 与 file plan 是正交的：

- file plan 决定 `<rendered_dir>` 下哪些文件需要写；
- container plan 决定哪些容器需要 `create / recreate / keep / remove`。

重建判定是**内容寻址**的（最小扰动原则），身份哈希的输入是
`ContainerDefinition.payload`——即将发给 Docker Engine API 的最终参数集
（见 `agent.planner.definition`），不是 schema 序列化：

- 容器缺失 -> create；
- 容器 `dn42.config_hash` label 与期望定义哈希不一致 -> recreate
  （有上一份已应用定义时，reason 给出**字段级 diff**）；
- 容器存在且哈希一致但状态非 running -> recreate；
- 观察到的受管容器不在期望 enabled 集合里 -> **remove**（孤儿清理：
  服务被禁用/移除/改名后旧容器不再永久残留）；
- 否则 keep。

之后做**依赖传播**：某服务被 (re)create 时，所有（传递）依赖它的服务也必须
recreate——`network_mode: service:X` 引用的是具体容器，X 重建后旧引用悬空；
`depends_on` 的启动顺序同理。传播是决策，必须发生在 plan 里而不是执行端，
执行端只许照单执行。

挂载进容器的配置文件（bird/wireguard）变化不触发重建，由本机收敛热加载。
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from dn42_schemas import (
    DesiredState,
    ObservedContainer,
    RuntimeResourceStatus,
    RuntimeServiceSpec,
)

from ..core.naming import node_project_name
from .definition import ContainerDefinition, build_node_definitions, diff_payload_keys


class ContainerAction(str, Enum):
    """容器层操作类型。"""

    CREATE = "create"
    RECREATE = "recreate"
    KEEP = "keep"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class ContainerStep:
    """容器层 plan 的单步动作。

    ``definition`` 是计划与执行的同源载体：CREATE/RECREATE/KEEP 步骤都
    携带完整容器定义，执行端**只许**按它物化容器；REMOVE（孤儿清理）
    没有期望定义，``service_name`` 也为 None。
    """

    service_name: str | None
    container_name: str
    action: ContainerAction
    reason: str
    definition: ContainerDefinition | None = None


@dataclass(frozen=True, slots=True)
class ContainerPlan:
    """容器层 plan 集合。"""

    project_name: str
    steps: list[ContainerStep] = field(default_factory=list)

    @property
    def to_recreate(self) -> list[ContainerStep]:
        return [step for step in self.steps if step.action == ContainerAction.RECREATE]

    @property
    def to_create(self) -> list[ContainerStep]:
        return [step for step in self.steps if step.action == ContainerAction.CREATE]

    @property
    def to_remove(self) -> list[ContainerStep]:
        return [step for step in self.steps if step.action == ContainerAction.REMOVE]

    @property
    def desired_hashes(self) -> dict[str, str]:
        """容器名 -> 期望定义哈希（供同源上报/对账使用）。"""

        return {
            step.container_name: step.definition.config_hash
            for step in self.steps
            if step.definition is not None
        }


def build_container_plan(
    state: DesiredState,
    observed: list[ObservedContainer],
    *,
    rendered_dir: Path,
    previous_definitions: Mapping[str, dict[str, Any]] | None = None,
) -> ContainerPlan:
    """计算容器层 plan（含依赖传播与孤儿清理后的最终动作）。

    ``previous_definitions`` 是上次成功 apply 后落盘的定义记录
    （`agent.apply.definition_store`），仅用于把 recreate 的 reason 提升为
    字段级 diff；缺失时降级为哈希对比描述，不影响判定正确性。
    """

    project = node_project_name(state)
    definitions = build_node_definitions(state, rendered_dir)
    observed_by_name = {container.name: container for container in observed}
    enabled = [service for service in state.runtime.services if service.enabled]
    previous = previous_definitions or {}

    decisions: dict[str, tuple[ContainerAction, str]] = {}
    for service in enabled:
        definition = definitions[service.name]
        existing = observed_by_name.get(definition.container_name)
        decisions[service.name] = _decide_action(
            definition, existing, previous.get(definition.container_name)
        )

    _propagate_dependencies(enabled, decisions)

    steps = [
        ContainerStep(
            service_name=service.name,
            container_name=definitions[service.name].container_name,
            action=decisions[service.name][0],
            reason=decisions[service.name][1],
            definition=definitions[service.name],
        )
        for service in enabled
    ]

    desired_names = {definition.container_name for definition in definitions.values()}
    for container in observed:
        if container.name in desired_names:
            continue
        steps.append(
            ContainerStep(
                service_name=None,
                container_name=container.name,
                action=ContainerAction.REMOVE,
                reason="managed container is no longer desired",
            )
        )
    return ContainerPlan(project_name=project, steps=steps)


def _propagate_dependencies(
    services: list[RuntimeServiceSpec],
    decisions: dict[str, tuple[ContainerAction, str]],
) -> None:
    """把 (re)create 沿 `depends_on` / `network_mode: service:X` 传递给依赖方。"""

    def _dependencies(service: RuntimeServiceSpec) -> set[str]:
        deps = {dep for dep in service.depends_on if dep in decisions}
        if service.network_mode and service.network_mode.startswith("service:"):
            target = service.network_mode.split(":", 1)[1]
            if target in decisions:
                deps.add(target)
        return deps

    changed = True
    while changed:
        changed = False
        for service in services:
            action, _reason = decisions[service.name]
            if action != ContainerAction.KEEP:
                continue
            rebuilt = sorted(
                dep
                for dep in _dependencies(service)
                if decisions[dep][0] in (ContainerAction.CREATE, ContainerAction.RECREATE)
            )
            if rebuilt:
                decisions[service.name] = (
                    ContainerAction.RECREATE,
                    f"dependency recreated: {', '.join(rebuilt)}",
                )
                changed = True


def _decide_action(
    definition: ContainerDefinition,
    existing: ObservedContainer | None,
    previous: dict[str, Any] | None,
) -> tuple[ContainerAction, str]:
    if existing is None:
        return ContainerAction.CREATE, "container missing"
    desired_hash = definition.config_hash
    if existing.config_hash != desired_hash:
        return ContainerAction.RECREATE, _drift_reason(definition, existing, previous)
    if existing.status != RuntimeResourceStatus.RUNNING:
        return ContainerAction.RECREATE, f"observed status={existing.status.value}"
    if existing.healthy is False:
        # 容器 Up 但健康检查持续失败 = 服务级死亡(如 bird daemon 死在 ``sleep infinity``
        # 的容器里、CoreDNS 绑不上地址崩溃循环)。config_hash + Docker 状态都看不出这种
        # 运行时漂移,据存活探针补救:重建容器。``healthy is None``(无探活/未确认)不触发,
        # 只在探针**明确判失败**时才动手,避免误杀。
        return ContainerAction.RECREATE, "running but unhealthy (service liveness probe failing)"
    return ContainerAction.KEEP, "definition hash matches"


def _drift_reason(
    definition: ContainerDefinition,
    existing: ObservedContainer,
    previous: dict[str, Any] | None,
) -> str:
    """哈希不一致时产出尽量可解释的 reason。

    上次落盘的定义记录与容器 label 哈希吻合时，才能确信它就是容器的
    现行定义，diff 才有意义；否则退回纯哈希描述。
    """

    if (
        previous is not None
        and previous.get("config_hash") == existing.config_hash
        and isinstance(previous.get("payload"), dict)
    ):
        changed = diff_payload_keys(previous["payload"], definition.payload)
        if changed:
            return f"definition changed: {', '.join(changed)}"
    return (
        f"definition drift (observed={existing.config_hash}, desired={definition.config_hash})"
    )


__all__ = ["ContainerAction", "ContainerPlan", "ContainerStep", "build_container_plan"]
