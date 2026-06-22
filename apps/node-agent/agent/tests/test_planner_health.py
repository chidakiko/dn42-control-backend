from __future__ import annotations

"""agent 侧 “规划器 + reconcile” 的单元测试。

agent 拉到新 DesiredState 后，需要把它与当前 RuntimeSnapshot 对齐，产出
一份可执行的差异计划，以及一份可上报给控制面的健康报告。本文件
锁定以下不变量：

* ``build_container_plan``：期望服务在快照中不存在 → ``CREATE``；
  运行中但定义哈希不同 → ``RECREATE``（有上次定义记录时 reason 为
  字段级 diff）；运行中且一致 → ``KEEP``；观察到的受管容器不在期望
  集合 → ``REMOVE``（孤儿清理）。
* 身份哈希的输入是 `ContainerDefinition.payload`（最终 Engine API 参数），
  generation 递增绝不触发重建。
* ``build_reconciliation_report``：汇总各资源 (容器 / WireGuard 接口 /
  BGP 会话) 的运行状态与期望状态之间的偏移；容器哈希维度与计划同源
  （`desired_hashes` 来自 ContainerPlan）。
* fake observer 使测试不依赖真实 docker / 内核接口，可在任意平台跑。
"""

from pathlib import Path

from dn42_schemas import (
    ApplyStatus,
    DriftSeverity,
    ObservationStatus,
    ObservedBgpProtocol,
    ObservedContainer,
    ObservedWireGuardInterface,
    RuntimeResourceStatus,
    RuntimeSnapshot,
    ServiceRole,
)
from dn42_schemas.testing import build_hkg1_example_state

from agent.core.naming import node_project_name, service_container_name
from agent.health.reconcile import build_reconciliation_report
from agent.planner.container_plan import ContainerAction, build_container_plan
from agent.planner.definition import build_node_definitions
from agent.collectors.snapshot import build_runtime_snapshot
from agent.collectors.docker import DockerObserver, ObservedProject

_RENDERED_DIR = Path("rendered")  # 定义构建只做路径解析，无需目录真实存在


class _FakeObserver(DockerObserver):
    def __init__(self, observed: ObservedProject) -> None:
        super().__init__(docker_factory=lambda: None)
        self._observed = observed

    def observe_project(self, state):  # type: ignore[override]
        return self._observed


def _running_container(name: str, role: ServiceRole, config_hash: str | None) -> ObservedContainer:
    return ObservedContainer(
        name=name,
        role=role,
        config_hash=config_hash,
        status=RuntimeResourceStatus.RUNNING,
        healthy=True,
    )


def _hashes(state) -> dict[str, str]:
    """容器名 -> 期望定义哈希（与 planner 同一来源）。"""

    return {
        definition.container_name: definition.config_hash
        for definition in build_node_definitions(state, _RENDERED_DIR).values()
    }


def _plan(state, observed, previous_definitions=None):
    return build_container_plan(
        state, observed, rendered_dir=_RENDERED_DIR, previous_definitions=previous_definitions
    )


def test_container_plan_marks_missing_as_create_and_running_as_keep() -> None:
    state = build_hkg1_example_state()
    project = node_project_name(state)
    hashes = _hashes(state)
    bird_name = service_container_name(project, "dn42-bird-router")
    netns_name = service_container_name(project, "dn42-router-netns")
    rpki_name = service_container_name(project, "dn42-rpki-cache")

    # rpki-cache 不依赖任何待重建服务：观测一致即 KEEP；
    # netns 缺失为 CREATE。
    observed = [
        _running_container(rpki_name, ServiceRole.RPKI_CACHE, hashes[rpki_name]),
        _running_container(bird_name, ServiceRole.BIRD_ROUTER, hashes[bird_name]),
    ]

    plan = _plan(state, observed)
    actions = {step.container_name: step.action for step in plan.steps}

    assert actions[rpki_name] == ContainerAction.KEEP
    assert actions[netns_name] == ContainerAction.CREATE


def test_container_plan_propagates_recreate_to_dependents() -> None:
    """依赖传播是决策层职责（review 缺陷 A 回归锁）。

    router-netns 缺失要 CREATE 时，所有经 `depends_on` / `network_mode:
    service:X`（传递）依赖它的服务即使观测一致也必须 RECREATE——
    `network_mode=container:<旧容器>` 在依赖重建后会悬空。
    """

    state = build_hkg1_example_state()
    project = node_project_name(state)
    hashes = _hashes(state)
    bird_name = service_container_name(project, "dn42-bird-router")
    wg_name = service_container_name(project, "dn42-wg-gateway")
    netns_name = service_container_name(project, "dn42-router-netns")

    # netns 缺失；bird / wg 观测与期望完全一致。
    observed = [
        _running_container(bird_name, ServiceRole.BIRD_ROUTER, hashes[bird_name]),
        _running_container(wg_name, ServiceRole.WG_GATEWAY, hashes[wg_name]),
    ]

    plan = _plan(state, observed)
    actions = {step.container_name: step.action for step in plan.steps}
    reasons = {step.container_name: step.reason for step in plan.steps}

    assert actions[netns_name] == ContainerAction.CREATE
    assert actions[wg_name] == ContainerAction.RECREATE
    assert actions[bird_name] == ContainerAction.RECREATE
    assert "dependency recreated" in reasons[wg_name]


def test_container_plan_marks_definition_drift_as_recreate() -> None:
    state = build_hkg1_example_state()
    project = node_project_name(state)
    netns_name = service_container_name(project, "dn42-router-netns")

    observed = [_running_container(netns_name, ServiceRole.ROUTER_NETNS, "deadbeefdeadbeef")]

    plan = _plan(state, observed)
    step = next(item for item in plan.steps if item.container_name == netns_name)

    assert step.action == ContainerAction.RECREATE
    assert "definition drift" in step.reason


def test_container_plan_explains_drift_with_field_diff_when_record_matches() -> None:
    """有上次定义记录且与容器 label 哈希吻合时，reason 必须是字段级 diff。"""

    state = build_hkg1_example_state()
    project = node_project_name(state)
    netns_name = service_container_name(project, "dn42-router-netns")
    definitions = build_node_definitions(state, _RENDERED_DIR)
    current = definitions["dn42-router-netns"]

    # 伪造"上次应用"的定义：command 不同 → 哈希不同。
    old_payload = dict(current.payload)
    old_payload["command"] = ["sleep", "1d"]
    from agent.planner.definition import payload_hash

    old_hash = payload_hash(old_payload)
    observed = [_running_container(netns_name, ServiceRole.ROUTER_NETNS, old_hash)]
    previous = {netns_name: {"config_hash": old_hash, "payload": old_payload}}

    plan = _plan(state, observed, previous_definitions=previous)
    step = next(item for item in plan.steps if item.container_name == netns_name)

    assert step.action == ContainerAction.RECREATE
    assert step.reason == "definition changed: command"


def test_container_plan_removes_orphan_managed_containers() -> None:
    """被禁用/移除服务的旧容器必须列为 REMOVE，不再永久残留。"""

    state = build_hkg1_example_state()
    hashes = _hashes(state)
    orphan = _running_container("dn42-edge1-dn42-old-service-1", ServiceRole.DNS, "cafe")
    observed = [orphan] + [
        _running_container(name, ServiceRole.BIRD_ROUTER, value) for name, value in hashes.items()
    ]

    plan = _plan(state, observed)
    removals = plan.to_remove

    assert [step.container_name for step in removals] == ["dn42-edge1-dn42-old-service-1"]
    assert removals[0].service_name is None
    assert removals[0].definition is None
    # 期望服务本身不受孤儿影响。
    assert all(
        step.action == ContainerAction.KEEP
        for step in plan.steps
        if step.service_name is not None
    )


def test_container_plan_keeps_containers_across_generation_bump() -> None:
    """generation 递增本身绝不触发任何容器重建——最小扰动的核心不变量。"""

    state = build_hkg1_example_state()
    observed = [
        _running_container(name, ServiceRole.BIRD_ROUTER, value)
        for name, value in _hashes(state).items()
    ]

    bumped = state.model_copy(update={"generation": state.generation + 1})
    plan = _plan(bumped, observed)

    assert all(step.action == ContainerAction.KEEP for step in plan.steps)


def test_runtime_snapshot_uses_observer_and_reports_applied_generation() -> None:
    state = build_hkg1_example_state()
    project = node_project_name(state)
    hashes = _hashes(state)
    netns_name = service_container_name(project, "dn42-router-netns")
    bird_name = service_container_name(project, "dn42-bird-router")
    observer = _FakeObserver(
        ObservedProject(
            project_name=project,
            containers=[
                _running_container(netns_name, ServiceRole.ROUTER_NETNS, hashes[netns_name]),
                _running_container(bird_name, ServiceRole.BIRD_ROUTER, hashes[bird_name]),
            ],
        )
    )

    snapshot = build_runtime_snapshot(
        state, applied_generation=state.generation, docker_observer=observer
    )

    assert snapshot.node_id == state.node.node_id
    assert snapshot.generation == state.generation
    assert len(snapshot.containers) == 2


def test_reconciliation_report_flags_missing_containers_as_critical_drift() -> None:
    state = build_hkg1_example_state()
    project = node_project_name(state)
    hashes = _hashes(state)
    netns_name = service_container_name(project, "dn42-router-netns")

    snapshot = build_runtime_snapshot(
        state,
        applied_generation=state.generation,
        docker_observer=_FakeObserver(
            ObservedProject(
                project_name=project,
                containers=[
                    _running_container(netns_name, ServiceRole.ROUTER_NETNS, hashes[netns_name])
                ],
            )
        ),
    )

    report = build_reconciliation_report(
        state, snapshot, apply_status=ApplyStatus.SUCCEEDED, desired_hashes=hashes
    )

    assert report.status == ApplyStatus.DEGRADED  # 缺失服务 → 降级
    critical = [item for item in report.drift if item.severity == DriftSeverity.CRITICAL]
    assert any("dn42-bird-router" in item.name for item in critical)


def test_reconciliation_report_flags_hash_drift_as_warning() -> None:
    """容器在跑但身份哈希与计划期望不符 → WARNING drift（与计划同源）。"""

    state = build_hkg1_example_state()
    project = node_project_name(state)
    hashes = _hashes(state)
    containers = [
        _running_container(name, ServiceRole.BIRD_ROUTER, value) for name, value in hashes.items()
    ]
    netns_name = service_container_name(project, "dn42-router-netns")
    containers = [
        _running_container(netns_name, ServiceRole.ROUTER_NETNS, "deadbeef")
        if item.name == netns_name
        else item
        for item in containers
    ]
    snapshot = build_runtime_snapshot(
        state,
        applied_generation=state.generation,
        docker_observer=_FakeObserver(
            ObservedProject(project_name=project, containers=containers)
        ),
    )

    report = build_reconciliation_report(
        state, snapshot, apply_status=ApplyStatus.SUCCEEDED, desired_hashes=hashes
    )

    drift = [item for item in report.drift if item.name == netns_name]
    assert drift and drift[0].severity == DriftSeverity.WARNING
    assert drift[0].desired == hashes[netns_name]


def _full_running_snapshot(state) -> RuntimeSnapshot:
    project = node_project_name(state)
    containers = [
        _running_container(name, ServiceRole.BIRD_ROUTER, value)
        for name, value in _hashes(state).items()
    ]
    return build_runtime_snapshot(
        state,
        applied_generation=state.generation,
        docker_observer=_FakeObserver(
            ObservedProject(project_name=project, containers=containers)
        ),
    )


def _report(state, snapshot, **fields):
    return build_reconciliation_report(
        state,
        snapshot,
        apply_status=ApplyStatus.SUCCEEDED,
        desired_hashes=_hashes(state),
        **fields,
    )


def test_reconciliation_report_succeeded_when_all_containers_running() -> None:
    state = build_hkg1_example_state()
    snapshot = _full_running_snapshot(state)

    report = _report(state, snapshot)

    assert report.status == ApplyStatus.SUCCEEDED
    assert report.drift == []


def _with_observations(snapshot: RuntimeSnapshot, **fields) -> RuntimeSnapshot:
    # 设了 wireguard_interfaces / bgp_protocols 即默认视作"已采集成功"（OBSERVED），
    # 除非显式覆盖对应 observation 字段。
    derived: dict = {}
    if "wireguard_interfaces" in fields and "wireguard_observation" not in fields:
        derived["wireguard_observation"] = ObservationStatus.OBSERVED.value
    if "bgp_protocols" in fields and "bgp_observation" not in fields:
        derived["bgp_observation"] = ObservationStatus.OBSERVED.value
    return RuntimeSnapshot.model_validate(
        {**snapshot.model_dump(mode="json"), **derived, **fields}
    )


def test_reconciliation_ignores_wireguard_and_bgp_when_not_observed() -> None:
    state = build_hkg1_example_state()
    snapshot = _full_running_snapshot(state)

    report = _report(state, snapshot)

    assert report.drift == []


def test_reconciliation_flags_missing_wireguard_interface() -> None:
    state = build_hkg1_example_state()
    base = _full_running_snapshot(state)
    snapshot = _with_observations(
        base,
        wireguard_interfaces=[
            ObservedWireGuardInterface(name="as4242420001", peer_count=2),
        ],
    )

    report = _report(state, snapshot)

    wg_drift = [item for item in report.drift if item.component == "wireguard"]
    assert any(
        item.name == "igp-edge2" and item.severity == DriftSeverity.CRITICAL
        for item in wg_drift
    )
    assert report.status == ApplyStatus.DEGRADED


def test_reconciliation_flags_wireguard_without_peers() -> None:
    state = build_hkg1_example_state()
    base = _full_running_snapshot(state)
    snapshot = _with_observations(
        base,
        wireguard_interfaces=[
            ObservedWireGuardInterface(name="as4242420001", peer_count=0),
            ObservedWireGuardInterface(name="igp-edge2", peer_count=1),
        ],
    )

    report = _report(state, snapshot)

    no_peer = [
        item
        for item in report.drift
        if item.component == "wireguard" and item.name == "as4242420001"
    ]
    assert no_peer and no_peer[0].severity == DriftSeverity.WARNING
    assert report.status == ApplyStatus.SUCCEEDED  # 仅 WARNING 不降级


def test_reconciliation_flags_bgp_not_established() -> None:
    state = build_hkg1_example_state()
    base = _full_running_snapshot(state)
    snapshot = _with_observations(
        base,
        bgp_protocols=[
            ObservedBgpProtocol(
                name="demopeer_v4",
                session="demopeer_4242420001_ex01_v4",
                state="Active",
            ),
            ObservedBgpProtocol(
                name="demopeer_v6",
                session="demopeer_4242420001_ex01_v6",
                state="Established",
            ),
        ],
    )

    report = _report(state, snapshot)

    bgp_drift = [item for item in report.drift if item.component == "bgp"]
    assert len(bgp_drift) == 1
    assert bgp_drift[0].name == "demopeer_4242420001_ex01_v4"
    assert bgp_drift[0].severity == DriftSeverity.WARNING


def test_observed_but_empty_wireguard_flags_all_expected_missing() -> None:
    """采集成功但为空（隧道全没起来）+ 期望存在接口 → 每个判 CRITICAL。

    这是假绿核心修复：旧逻辑 `if not wireguard_interfaces: return []` 会把
    "采集成功且为空"静默跳过，显示无 drift。
    """

    state = build_hkg1_example_state()
    base = _full_running_snapshot(state)
    snapshot = _with_observations(
        base,
        wireguard_interfaces=[],
        wireguard_observation=ObservationStatus.OBSERVED.value,
    )

    report = _report(state, snapshot)

    wg_critical = [
        item
        for item in report.drift
        if item.component == "wireguard" and item.severity == DriftSeverity.CRITICAL
    ]
    assert {item.name for item in wg_critical} == {"as4242420001", "igp-edge2"}
    assert report.status == ApplyStatus.DEGRADED


def test_unavailable_wireguard_collection_warns_not_green() -> None:
    """采集失败（UNAVAILABLE）+ 期望存在 WG 接口 → 一条 WARNING，状态不绿但不降级。

    避免"容器刚 recreate、wg 还没拉起、exec 失败"被静默当成健康。
    """

    state = build_hkg1_example_state()
    base = _full_running_snapshot(state)
    snapshot = _with_observations(
        base,
        wireguard_observation=ObservationStatus.UNAVAILABLE.value,
    )

    report = _report(state, snapshot)

    wg_drift = [item for item in report.drift if item.component == "wireguard"]
    assert len(wg_drift) == 1
    assert wg_drift[0].severity == DriftSeverity.WARNING
    assert wg_drift[0].observed == "unavailable"
    # 采集失败 ≠ 故障：不武断降级，但 drift_count > 0 让运维看得见。
    assert report.status == ApplyStatus.SUCCEEDED
