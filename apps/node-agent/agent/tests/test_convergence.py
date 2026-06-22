from __future__ import annotations

"""本机收敛的决策（planner）与执行（apply）分层测试。

决策端 ``build_convergence_plan`` 锁定**定向收敛**的核心不变量：

* 无差异（all keep + file plan 全 noop）→ 空计划；
* 仅 `wireguard/<iface>.conf` 变化 → 只同步该接口，不重放全量、不触碰 BIRD；
* `wireguard/<iface>.conf` 被删除 → 拆除该接口；
* 仅 `bird/*` 变化 → 只做 ``birdc configure``；
* wg-gateway / router-netns 被 (re)create → 整体重放（netns 隧道已丢失）；
* bird-router 被重建 → 不做 ``birdc configure``（启动即加载新配置）。

执行端 ``execute_convergence_plan`` 锁定：

* 严格按计划翻译成容器内 exec（注入式 ``ContainerExec``），空计划零命令；
* 失败 best-effort：收进结果，不抛出。
"""

from dn42_runtime import PlanAction
from dn42_schemas import InterfaceKind, ServiceRole
from dn42_schemas.testing import build_hkg1_example_state

from agent.apply.convergence import execute_convergence_plan
from agent.core.naming import node_project_name, service_container_by_role, service_container_name
from agent.planner import (
    ContainerAction,
    ContainerPlan,
    ConvergenceAction,
    ConvergenceKind,
    ConvergencePlan,
    build_convergence_plan,
)
from agent.planner.container_plan import ContainerStep


class _RecordingExec:
    """记录 (container, argv) 的假 ContainerExec。"""

    def __init__(self, returncode: int = 0) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._returncode = returncode

    def run(self, container: str, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append((container, argv))
        return self._returncode, "", ""

    def put_file(self, *args, **kwargs) -> None:
        raise AssertionError("convergence 不应推送文件")


def _container_plan(state, action: ContainerAction) -> ContainerPlan:
    project = node_project_name(state)
    steps = [
        ContainerStep(
            service_name=service.name,
            container_name=service_container_name(project, service.name),
            action=action,
            reason="test",
        )
        for service in state.runtime.services
        if service.enabled
    ]
    return ContainerPlan(project_name=project, steps=steps)


def _noop_actions() -> list[PlanAction]:
    return [PlanAction("noop", "bird/bird.conf"), PlanAction("noop", "wireguard/wg-a.conf")]


# -------- 决策 --------


def test_no_changes_yields_empty_plan() -> None:
    state = build_hkg1_example_state()
    plan = build_convergence_plan(
        state, _container_plan(state, ContainerAction.KEEP), _noop_actions()
    )
    assert plan.actions == []


def test_changed_wireguard_conf_syncs_only_that_interface() -> None:
    state = build_hkg1_example_state()
    actions = _noop_actions() + [PlanAction("update", "wireguard/as4242420001.conf")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)

    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    assert wg_container is not None
    assert plan.actions == [
        ConvergenceAction(
            kind=ConvergenceKind.WG_SYNC_INTERFACE,
            container=wg_container,
            interface="as4242420001",
        )
    ]


def test_changed_apply_script_syncs_interface_without_wg_conf_change() -> None:
    """纯接口地址 / 路由 / MTU 变更只改 apply 脚本、不改 wg conf——仍须重同步该接口。

    这正是历史缺口：只看 `wireguard/<iface>.conf` 会漏掉纯地址变更，导致改了
    地址却不触发 re-sync。修复后 apply 脚本变化也触发该接口同步。
    """

    state = build_hkg1_example_state()
    iface = next(i.name for i in state.interfaces if i.kind == InterfaceKind.WIREGUARD)
    actions = _noop_actions() + [PlanAction("update", f"scripts/wg/apply-{iface}.sh")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)

    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    assert plan.actions == [
        ConvergenceAction(
            kind=ConvergenceKind.WG_SYNC_INTERFACE,
            container=wg_container,
            interface=iface,
        )
    ]


def test_changed_loopback_script_syncs_loopback() -> None:
    """loopback 地址只在 apply-dn42-lo.sh 里——该脚本变化须重跑 loopback 同步。"""

    state = build_hkg1_example_state()
    actions = _noop_actions() + [PlanAction("update", "scripts/wg/apply-dn42-lo.sh")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)

    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    assert plan.actions == [
        ConvergenceAction(kind=ConvergenceKind.WG_SYNC_LOOPBACK, container=wg_container)
    ]


def test_changed_dns_anycast_script_syncs_that_dummy() -> None:
    """dns-anycast 任播地址只在 apply-dns-anycast.sh 里——脚本变化须重同步该 dummy（与
    WG 接口一样按名走 WG_SYNC_INTERFACE，执行端跑 apply-dns-anycast.sh）。"""

    state = build_hkg1_example_state()  # DNS 启用样例，含 dns-anycast dummy
    actions = _noop_actions() + [PlanAction("update", "scripts/wg/apply-dns-anycast.sh")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)

    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    assert plan.actions == [
        ConvergenceAction(
            kind=ConvergenceKind.WG_SYNC_INTERFACE,
            container=wg_container,
            interface="dns-anycast",
        )
    ]


def test_removed_dns_anycast_script_tears_down_dummy() -> None:
    """节点退订 DNS → dns-anycast 从期望态消失、apply 脚本被删 → ip link del 拆除该 dummy，
    否则任播地址滞留主机继续吸引流量（黑洞）。dummy 无 wg conf，故拆除以 apply 脚本删除为准。"""

    base = build_hkg1_example_state()
    data = base.model_dump(mode="json")
    data["dns"] = None
    state = base.__class__.model_validate(data)  # 归一化后已无 dns-anycast 接口
    assert all(i.name != "dns-anycast" for i in state.interfaces)

    actions = _noop_actions() + [PlanAction("delete", "scripts/wg/apply-dns-anycast.sh")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)

    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    assert plan.actions == [
        ConvergenceAction(
            kind=ConvergenceKind.WG_REMOVE_INTERFACE,
            container=wg_container,
            interface="dns-anycast",
        )
    ]


def test_conf_and_apply_script_change_dedup_to_single_sync() -> None:
    """同一接口的 wg conf 与 apply 脚本同时变化 → 只产出一次接口同步（去重）。"""

    state = build_hkg1_example_state()
    iface = next(i.name for i in state.interfaces if i.kind == InterfaceKind.WIREGUARD)
    actions = _noop_actions() + [
        PlanAction("update", f"wireguard/{iface}.conf"),
        PlanAction("update", f"scripts/wg/apply-{iface}.sh"),
    ]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)

    syncs = [a for a in plan.actions if a.kind == ConvergenceKind.WG_SYNC_INTERFACE]
    assert len(syncs) == 1
    assert syncs[0].interface == iface


def test_aggregate_apply_script_change_does_not_sync_phantom_interface() -> None:
    """聚合入口 apply-all-wg.sh 不是单接口脚本，变化不应产出任何接口同步。"""

    state = build_hkg1_example_state()
    actions = _noop_actions() + [PlanAction("update", "scripts/wg/apply-all-wg.sh")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)
    assert plan.actions == []


def test_removed_wireguard_conf_removes_interface() -> None:
    state = build_hkg1_example_state()
    actions = _noop_actions() + [PlanAction("delete", "wireguard/dead-peer.conf")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)

    assert [a.kind for a in plan.actions] == [ConvergenceKind.WG_REMOVE_INTERFACE]
    assert plan.actions[0].interface == "dead-peer"


def test_bird_change_only_reloads_bird() -> None:
    state = build_hkg1_example_state()
    actions = _noop_actions() + [PlanAction("update", "bird/dn42_peers.conf")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.KEEP), actions)

    bird_container = service_container_by_role(state, ServiceRole.BIRD_ROUTER)
    assert bird_container is not None
    assert plan.actions == [
        ConvergenceAction(kind=ConvergenceKind.BIRD_RELOAD, container=bird_container)
    ]


def test_wg_gateway_recreate_expands_full_replay_in_python() -> None:
    """netns/wg 重建 → 计划层在 Python 里展开为 loopback + 其余 dummy（dns-anycast）+ 每个
    WG 接口的独立动作（不再是一个容器内 bash glob 整体重放）。"""

    state = build_hkg1_example_state()
    plan = build_convergence_plan(
        state, _container_plan(state, ContainerAction.RECREATE), _noop_actions()
    )

    # 第一个 WG 动作是 loopback，且唯一。
    loopbacks = [a for a in plan.actions if a.kind == ConvergenceKind.WG_SYNC_LOOPBACK]
    assert len(loopbacks) == 1

    # 之后先是每个非 loopback dummy（dns-anycast，排序），再是每个 WireGuard 接口（排序），
    # 各一个独立 SYNC 动作（与渲染端 apply-<name>.sh 枚举同源）。
    dummies = sorted(
        i.name
        for i in state.interfaces
        if i.kind == InterfaceKind.DUMMY and i.name != "dn42-lo"
    )
    wgs = sorted(i.name for i in state.interfaces if i.kind == InterfaceKind.WIREGUARD)
    synced = [a.interface for a in plan.actions if a.kind == ConvergenceKind.WG_SYNC_INTERFACE]
    assert synced == dummies + wgs
    assert "dns-anycast" in dummies  # DNS 启用样例确有任播 dummy
    # 全量重放不应出现按接口拆除动作。
    assert not any(a.kind == ConvergenceKind.WG_REMOVE_INTERFACE for a in plan.actions)


def test_bird_recreate_skips_birdc_configure() -> None:
    """bird-router 重建后启动即加载新配置，不需要（也不应）再 reload。"""

    state = build_hkg1_example_state()
    actions = _noop_actions() + [PlanAction("update", "bird/bird.conf")]
    plan = build_convergence_plan(state, _container_plan(state, ContainerAction.RECREATE), actions)
    assert not any(a.kind == ConvergenceKind.BIRD_RELOAD for a in plan.actions)


# -------- 执行 --------


def test_execute_empty_plan_runs_nothing() -> None:
    container_exec = _RecordingExec()
    result = execute_convergence_plan(ConvergencePlan(), container_exec=container_exec)
    assert result.ok
    assert container_exec.calls == []


def test_execute_translates_actions_to_container_exec() -> None:
    state = build_hkg1_example_state()
    wg = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    bird = service_container_by_role(state, ServiceRole.BIRD_ROUTER)
    assert wg is not None
    assert bird is not None
    plan = ConvergencePlan(
        actions=[
            ConvergenceAction(kind=ConvergenceKind.BIRD_RELOAD, container=bird),
            ConvergenceAction(
                kind=ConvergenceKind.WG_SYNC_INTERFACE, container=wg, interface="wg-a"
            ),
            ConvergenceAction(
                kind=ConvergenceKind.WG_REMOVE_INTERFACE, container=wg, interface="wg-b"
            ),
        ]
    )

    container_exec = _RecordingExec()
    result = execute_convergence_plan(plan, container_exec=container_exec)

    assert result.ok
    assert container_exec.calls == [
        (bird, ["birdc", "configure"]),
        (wg, ["sh", "/opt/dn42/scripts/wg/apply-wg-a.sh"]),
        (wg, ["ip", "link", "del", "wg-b"]),
    ]


def test_replay_runs_each_script_independently_and_propagates_failure() -> None:
    """全量重放在 Python 里展开为 loopback + 逐接口的独立 exec，每步独立检查。

    单个接口脚本失败 → 该步 ok=False → 整体 ConvergenceResult.ok=False，
    不再被一个容器内 bash glob 整体吞掉。
    """

    state = build_hkg1_example_state()
    plan = build_convergence_plan(
        state, _container_plan(state, ContainerAction.RECREATE), _noop_actions()
    )
    wg = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    assert wg is not None

    # 让"第二个接口脚本"失败，其余成功。
    expected_ifaces = sorted(
        iface.name for iface in state.interfaces if iface.kind == InterfaceKind.WIREGUARD
    )
    failing_iface = expected_ifaces[1]
    failing_script = f"/opt/dn42/scripts/wg/apply-{failing_iface}.sh"

    class _SelectiveExec:
        def __init__(self) -> None:
            self.calls: list[tuple[str, list[str]]] = []

        def run(self, container: str, argv: list[str]) -> tuple[int, str, str]:
            self.calls.append((container, argv))
            return (1, "", "boom") if argv[-1] == failing_script else (0, "", "")

        def put_file(self, *args, **kwargs) -> None:
            raise AssertionError("convergence 不应推送文件")

    container_exec = _SelectiveExec()
    result = execute_convergence_plan(plan, container_exec=container_exec)

    # loopback 用固定脚本名拉起。
    assert (wg, ["sh", "/opt/dn42/scripts/wg/apply-dn42-lo.sh"]) in container_exec.calls
    # 每个接口各自一次独立 exec。
    for iface in expected_ifaces:
        assert (
            wg,
            ["sh", f"/opt/dn42/scripts/wg/apply-{iface}.sh"],
        ) in container_exec.calls
    # 单接口失败 → 整体不 ok，且失败精确定位到该步。
    assert not result.ok
    failed = [step for step in result.steps if not step.ok]
    assert len(failed) == 1
    assert failing_iface in failed[0].target


def test_execute_collects_failures_without_raising() -> None:
    state = build_hkg1_example_state()
    bird = service_container_by_role(state, ServiceRole.BIRD_ROUTER)
    assert bird is not None
    plan = ConvergencePlan(
        actions=[ConvergenceAction(kind=ConvergenceKind.BIRD_RELOAD, container=bird)]
    )

    result = execute_convergence_plan(plan, container_exec=_RecordingExec(returncode=1))

    assert not result.ok
    assert len(result.steps) == 1
    assert result.steps[0].error


def test_execute_survives_exec_exception() -> None:
    state = build_hkg1_example_state()
    bird = service_container_by_role(state, ServiceRole.BIRD_ROUTER)
    assert bird is not None
    plan = ConvergencePlan(
        actions=[ConvergenceAction(kind=ConvergenceKind.BIRD_RELOAD, container=bird)]
    )

    class _BoomExec:
        def run(self, container: str, argv: list[str]) -> tuple[int, str, str]:
            raise OSError("docker not available")

        def put_file(self, *args, **kwargs) -> None:
            raise AssertionError("unreachable")

    result = execute_convergence_plan(plan, container_exec=_BoomExec())
    assert not result.ok
    assert result.steps[0].returncode == -1
