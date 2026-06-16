from __future__ import annotations

"""本机收敛的**决策**：从 file plan 与 container plan 推导收敛动作清单。

收敛是"容器没重建、但挂载进去的配置变了"时的定向热加载。这里只做决策、
产出 `ConvergencePlan`；真正的 docker exec 在 `agent.apply.convergence` 里
照单执行。规则（与内容寻址容器身份配套）：

- **BIRD**：`bird/` 下有文件实际变化（create/update/delete）且 bird-router
  容器没有被 (re)create 时 -> `birdc configure` 热重载（重建出来的容器
  启动即加载新配置，无需再 reload）。
- **WireGuard**：
  - wg-gateway / router-netns 被 (re)create -> netns 内隧道已全部丢失，
    需要全量拉起。**编排在 Python 这里完成**：枚举 loopback + 每个 WireGuard
    接口，逐个产出独立的同步动作（不再交给容器内一个 bash glob 循环整体执行），
    执行层据此逐步发命令、逐步检查返回码，单接口失败不再被吞。
  - 否则**接口配置变化即热同步该接口**：触发口径是 `wireguard/<iface>.conf`
    （密钥 / 端口 / 对端）**或** `scripts/wg/apply-<iface>.sh`（地址 / 对端路由 /
    MTU）任一 create/update——后者尤其重要，因为接口地址只渲染进 apply 脚本、
    不进 wg conf，只看 wg conf 会漏掉纯地址变更，导致改了地址却不重同步。
    脚本是 `wg syncconf` + `ip addr/route replace` 就地热更新，不弹跳；
    `wireguard/<iface>.conf` delete -> 拆除该接口。
  - loopback（dn42-lo）地址只渲染进 `scripts/wg/apply-dn42-lo.sh`，该脚本
    create/update -> 重跑 loopback 同步（同样不依赖 wg conf）。
"""

from dataclasses import dataclass, field
from enum import Enum

from dn42_runtime import PlanAction
from dn42_schemas import DesiredState, InterfaceKind, ServiceRole

from ..core.naming import service_container_by_role
from .container_plan import ContainerAction, ContainerPlan

_WG_PREFIX = "wireguard/"
_WG_SUFFIX = ".conf"
_BIRD_PREFIX = "bird/"
# 接口/loopback 的地址、对端路由、MTU 只渲染进 apply 脚本，不进 wg conf。
_APPLY_PREFIX = "scripts/wg/apply-"
_APPLY_SUFFIX = ".sh"
_LOOPBACK_SCRIPT = "scripts/wg/apply-dn42-lo.sh"
# apply-dn42-lo.sh 走 loopback 专路；apply-all-wg.sh 是聚合入口，二者都不是单接口脚本。
_NON_INTERFACE_APPLY_NAMES = frozenset({"dn42-lo", "all-wg"})


class ConvergenceKind(str, Enum):
    """收敛动作类型。"""

    BIRD_RELOAD = "bird-reload"
    WG_SYNC_LOOPBACK = "wg-sync-loopback"
    WG_SYNC_INTERFACE = "wg-sync-interface"
    WG_REMOVE_INTERFACE = "wg-remove-interface"


@dataclass(frozen=True, slots=True)
class ConvergenceAction:
    """单个收敛动作：在哪个容器里、对什么目标、做什么。"""

    kind: ConvergenceKind
    container: str
    interface: str | None = None


@dataclass(frozen=True, slots=True)
class ConvergencePlan:
    """收敛动作清单（可为空：无差异时不执行任何命令）。"""

    actions: list[ConvergenceAction] = field(default_factory=list)


def build_convergence_plan(
    state: DesiredState,
    container_plan: ContainerPlan,
    file_actions: list[PlanAction],
) -> ConvergencePlan:
    """从同一份 file/container plan 推导收敛动作，保证三层计划同源。"""

    actions: list[ConvergenceAction] = []

    # ---- BIRD：只在配置实际变化且容器未重建时热重载 ----
    bird_container = service_container_by_role(state, ServiceRole.BIRD_ROUTER)
    if (
        bird_container is not None
        and _bird_files_changed(file_actions)
        and not _role_rebuilt(container_plan, state, ServiceRole.BIRD_ROUTER)
    ):
        actions.append(
            ConvergenceAction(kind=ConvergenceKind.BIRD_RELOAD, container=bird_container)
        )

    # ---- WireGuard ----
    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    if wg_container is None:
        return ConvergencePlan(actions=actions)

    netns_rebuilt = _role_rebuilt(container_plan, state, ServiceRole.ROUTER_NETNS)
    wg_rebuilt = _role_rebuilt(container_plan, state, ServiceRole.WG_GATEWAY)
    if netns_rebuilt or wg_rebuilt:
        # netns 内隧道全丢：在 Python 里展开"全量拉起" = loopback + 每个 WG 接口，
        # 每个接口一个独立的可检查动作（取代容器内 bash glob 整体重放）。
        actions.append(
            ConvergenceAction(kind=ConvergenceKind.WG_SYNC_LOOPBACK, container=wg_container)
        )
        for iface in _wireguard_interface_names(state):
            actions.append(
                ConvergenceAction(
                    kind=ConvergenceKind.WG_SYNC_INTERFACE,
                    container=wg_container,
                    interface=iface,
                )
            )
        return ConvergencePlan(actions=actions)

    # loopback 地址只在 apply-dn42-lo.sh 里，wg conf 看不到——脚本变化即重跑 loopback。
    if _loopback_address_changed(file_actions):
        actions.append(
            ConvergenceAction(kind=ConvergenceKind.WG_SYNC_LOOPBACK, container=wg_container)
        )

    changed, removed = _changed_wg_interfaces(state, file_actions)
    for iface in changed:
        actions.append(
            ConvergenceAction(
                kind=ConvergenceKind.WG_SYNC_INTERFACE,
                container=wg_container,
                interface=iface,
            )
        )
    for iface in removed:
        actions.append(
            ConvergenceAction(
                kind=ConvergenceKind.WG_REMOVE_INTERFACE,
                container=wg_container,
                interface=iface,
            )
        )
    return ConvergencePlan(actions=actions)


def _role_rebuilt(plan: ContainerPlan, state: DesiredState, role: ServiceRole) -> bool:
    target = {
        service.name
        for service in state.runtime.services
        if service.enabled and service.role == role
    }
    return any(
        step.service_name in target
        and step.action in (ContainerAction.CREATE, ContainerAction.RECREATE)
        for step in plan.steps
    )


def _wireguard_interface_names(state: DesiredState) -> list[str]:
    """枚举节点全部 WireGuard 接口名（排序），与渲染端 apply-<iface>.sh 同源。"""

    return sorted(
        iface.name
        for iface in state.interfaces
        if iface.kind == InterfaceKind.WIREGUARD
    )


def _bird_files_changed(actions: list[PlanAction]) -> bool:
    return any(
        action.path.startswith(_BIRD_PREFIX) and action.action != "noop"
        for action in actions
    )


def _changed_wg_interfaces(
    state: DesiredState, actions: list[PlanAction]
) -> tuple[list[str], list[str]]:
    """从 file plan 中提取需要同步 / 拆除的 WireGuard 接口名。

    同步触发口径并集：接口的 `wireguard/<iface>.conf`（密钥 / 端口 / 对端）**或**
    `scripts/wg/apply-<iface>.sh`（地址 / 对端路由 / MTU）任一 create/update。
    只看 wg conf 会漏掉纯地址变更（地址不进 wg conf），故必须同时看 apply 脚本。
    拆除仍以 `wireguard/<iface>.conf` 被删除为准（apply 脚本随之删除，避免重复计数）。
    """

    wg_names = {
        iface.name
        for iface in state.interfaces
        if iface.kind == InterfaceKind.WIREGUARD
    }
    changed: set[str] = set()
    removed: list[str] = []
    for action in actions:
        if action.action == "noop":
            continue
        conf_iface = _wg_conf_interface(action.path)
        if action.action in ("create", "update"):
            for iface in (conf_iface, _apply_script_interface(action.path)):
                if iface is not None and iface in wg_names:
                    changed.add(iface)
        elif action.action == "delete" and conf_iface is not None:
            removed.append(conf_iface)
    return sorted(changed), sorted(removed)


def _wg_conf_interface(path: str) -> str | None:
    """`wireguard/<iface>.conf` -> 接口名；其余路径返回 None。"""

    if not path.startswith(_WG_PREFIX) or not path.endswith(_WG_SUFFIX):
        return None
    iface = path[len(_WG_PREFIX) : -len(_WG_SUFFIX)]
    return iface if iface and "/" not in iface else None


def _apply_script_interface(path: str) -> str | None:
    """`scripts/wg/apply-<iface>.sh` -> 接口名；loopback / 聚合脚本 / 其余返回 None。"""

    if not path.startswith(_APPLY_PREFIX) or not path.endswith(_APPLY_SUFFIX):
        return None
    name = path[len(_APPLY_PREFIX) : -len(_APPLY_SUFFIX)]
    if not name or "/" in name or name in _NON_INTERFACE_APPLY_NAMES:
        return None
    return name


def _loopback_address_changed(actions: list[PlanAction]) -> bool:
    """loopback 地址脚本 `scripts/wg/apply-dn42-lo.sh` create/update。"""

    return any(
        action.path == _LOOPBACK_SCRIPT and action.action in ("create", "update")
        for action in actions
    )


__all__ = [
    "ConvergenceAction",
    "ConvergenceKind",
    "ConvergencePlan",
    "build_convergence_plan",
]
