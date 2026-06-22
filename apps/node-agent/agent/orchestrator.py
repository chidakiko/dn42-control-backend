from __future__ import annotations

"""Reconcile 薄协调器：source → render → observe → plan → execute → report。

每一阶段都有专属归属，协调器只负责把它们按序串起来：

1. **source**（`agent.sources`）：从控制面 / 本地文件 / 内置示例取状态；
   注册与 401 自愈在 Session 里，不在这里。
2. **render**（`agent.render`）：状态 → RenderedBundle。
3. **observe**（`agent.collectors`）：容器 + WG + BGP 只读观测。
4. **plan**（`agent.planner`）：★ 决策层一次性产出唯一权威 `ReconcilePlan`。
5. **execute**（`agent.apply`）：按 mode 深度严格照单执行。
6. **report**（本模块派生）：snapshot / reconciliation-report / apply-result
   全部从同一份计划 + 真实执行结果派生，经 Session 上报。

副作用一律通过 `Adapters` 进入；单测装配假 Adapters 即可，无需真实网络或容器。
"""

from dataclasses import dataclass, field
from typing import Any

from dn42_schemas import (
    ApplyResult,
    ApplyStatus,
    AppliedFileRecord,
    DesiredState,
    PlanSummary,
    ReconciliationReport,
    RuntimeSnapshot,
    ServiceRole,
)
from dn42_templates import bird_protocol_name

from dn42_runtime import ApplyOutcome as FileApplyOutcome

from .adapters import Adapters
from .apply.convergence import ConvergenceResult, execute_convergence_plan
from .apply.definition_store import load_container_definitions, persist_container_definitions
from .apply.executor import DeployResult
from .apply.writer import write_rendered_bundle
from .collectors.docker import DockerObserver, ObservedProject
from .collectors.network import BgpObserver, WireGuardObserver
from .collectors.snapshot import build_runtime_snapshot
from .metrics import load_metrics
from .core.clock import utc_now_iso
from .core.config import AgentConfig
from .core.errors import ControllerError
from .core.exec import container_output_runner
from .core.identity import LocalAgentIdentity, load_identity, save_identity
from .core.logging import get_logger
from .core.naming import service_container_by_role
from .core.paths import AgentPaths
from .desired_state.cache import save_cached_desired_state
from .health.reconcile import build_reconciliation_report
from .planner import ContainerPlan, ReconcilePlan, build_reconcile_plan
from .render.pipeline import RenderedBundle, render_state
from .secrets import build_wireguard_key_report, push_wireguard_key_to_container
from .sources import select_source

_LOGGER = get_logger("orchestrator")


@dataclass(slots=True)
class OrchestratorResult:
    """一次 reconcile 运行的完整摘要。"""

    source: str
    mode: str
    state: DesiredState
    rendered_files: int
    plan: ReconcilePlan
    apply_status: ApplyStatus
    snapshot: RuntimeSnapshot
    report: ReconciliationReport
    identity: LocalAgentIdentity
    deploy_result: DeployResult | None = None
    convergence: ConvergenceResult | None = None
    controller_acks: dict[str, Any] = field(default_factory=dict)

    @property
    def plan_summary(self) -> PlanSummary:
        return self.plan.file_plan.summary

    @property
    def container_plan(self) -> ContainerPlan:
        return self.plan.container_plan

    def summary(self) -> dict[str, Any]:
        """生成可 JSON 序列化的人类可读摘要。"""

        out: dict[str, Any] = {
            "node_id": self.state.node.node_id,
            "generation": self.state.generation,
            "source": self.source,
            "mode": self.mode,
            "rendered_files": self.rendered_files,
            "runtime_services": len([s for s in self.state.runtime.services if s.enabled]),
            "interfaces": len(self.state.interfaces),
            "bgp_sessions": len(self.state.bgp_sessions),
            "dns_enabled": bool(self.state.dns and self.state.dns.enabled),
            "plan_summary": self.plan_summary.model_dump(mode="json"),
            "container_plan": [
                {
                    "service": step.service_name,
                    "container": step.container_name,
                    "action": step.action.value,
                    "reason": step.reason,
                }
                for step in self.container_plan.steps
            ],
            "convergence_plan": [
                {
                    "kind": action.kind.value,
                    "container": action.container,
                    "interface": action.interface,
                }
                for action in self.plan.convergence_plan.actions
            ],
            "apply_status": self.apply_status.value,
            "report": self.report.model_dump(mode="json"),
        }
        if self.deploy_result is not None:
            out["deployment"] = self.deploy_result.raw
        if self.convergence is not None:
            out["convergence"] = self.convergence.to_dict()
        if self.controller_acks:
            out["controller"] = self.controller_acks
        return out


@dataclass(slots=True)
class _ExecutionOutcome:
    """execute 阶段的真实结果（与计划同源，供上报使用）。"""

    apply_status: ApplyStatus
    file_outcome: FileApplyOutcome | None = None
    deploy_result: DeployResult | None = None
    convergence: ConvergenceResult | None = None


class ReconcileOrchestrator:
    """单次 reconcile 的协调实现。"""

    def __init__(self, *, config: AgentConfig, adapters: Adapters) -> None:
        self._config = config
        self._adapters = adapters

    def run_once(self) -> OrchestratorResult:
        """执行一次 reconcile，返回结构化结果。"""

        config = self._config
        adapters = self._adapters

        # ---- source ----
        source = select_source(config, adapters.session)
        state = source.fetch()
        identity = self._load_identity(state)

        paths = AgentPaths(state_dir=config.state_dir, node_id=state.node.node_id)
        paths.ensure()
        rendered_dir = config.rendered_dir or paths.rendered_dir

        # ---- secret 兑现 + 注册一致性校验（apply 前，确保密钥同源、公钥不漂移）----
        self._sync_wireguard_keys(state, paths)

        # ---- render / observe / plan ----
        bundle: RenderedBundle = render_state(state)
        observed: ObservedProject = adapters.docker_observer.observe_project(state)
        plan = build_reconcile_plan(
            state,
            bundle,
            rendered_dir,
            observed.containers,
            previous_definitions=load_container_definitions(paths.container_definitions_dir),
        )

        # ---- execute ----
        execution = self._execute(plan, state, bundle, rendered_dir, paths)

        # ---- report ----
        post_observed = (
            adapters.docker_observer.observe_project(state)
            if config.mode != "plan-only"
            else observed
        )
        applied_generation = (
            state.generation
            if execution.apply_status == ApplyStatus.SUCCEEDED
            else identity.applied_generation
        )
        wireguard_observer, bgp_observer = self._build_network_observers(state, post_observed)
        snapshot = build_runtime_snapshot(
            state,
            applied_generation=applied_generation,
            docker_observer=_StaticObserver(post_observed),
            wireguard_observer=wireguard_observer,
            bgp_observer=bgp_observer,
            # 带上 agent 自观测（CPU/RSS/背景循环耗时）。读上一轮落盘的 metrics.json：
            # reconcile 计数是上一轮、CPU/RSS 是 self-monitor 最近一次，足够前端展示。
            metrics=load_metrics(paths.metrics_file),
        )
        report = build_reconciliation_report(
            state,
            snapshot,
            apply_status=execution.apply_status,
            desired_hashes=plan.container_plan.desired_hashes,
        )

        # desired state 缓存与上报无关，先落盘供离线兜底。
        save_cached_desired_state(state, paths.desired_state_file)

        # ---- 上报先于推进世代 ----
        # 必须先把 snapshot/report/apply-result 成功上报，才推进并持久化
        # applied_generation。否则"本地已推进、控制面没收到"会让这一代再也不触发
        # 重报（WS 去重与 hello 追赶都基于 applied_generation），控制面视图长期脱节。
        # _publish 失败会抛出，下面的世代推进被跳过，下一轮重试并重报。
        controller_acks = self._publish(state, plan, execution, snapshot, report)

        # 上报成功后，从 Session 取最新身份（_publish 可能触发 401 自愈轮换了
        # token），在最新身份上更新，避免用陈旧副本回写覆盖刚轮换的 token。
        identity = self._current_identity(state, paths)
        identity.node_id = state.node.node_id
        if execution.apply_status == ApplyStatus.SUCCEEDED:
            identity.applied_generation = state.generation
        identity.last_apply_status = report.status.value
        identity.last_apply_at = utc_now_iso()
        self._persist_identity(identity, paths)

        return OrchestratorResult(
            source=source.name,
            mode=config.mode,
            state=state,
            rendered_files=len(bundle.files),
            plan=plan,
            apply_status=execution.apply_status,
            snapshot=snapshot,
            report=report,
            identity=identity,
            deploy_result=execution.deploy_result,
            convergence=execution.convergence,
            controller_acks=controller_acks,
        )

    # ----- 阶段实现 -----

    def _load_identity(self, state: DesiredState) -> LocalAgentIdentity:
        session = self._adapters.session
        if session is not None:
            return session.ensure()
        paths = AgentPaths(self._config.state_dir, state.node.node_id)
        return load_identity(paths.identity_file)

    def _execute(
        self,
        plan: ReconcilePlan,
        state: DesiredState,
        bundle: RenderedBundle,
        rendered_dir,
        paths: AgentPaths,
    ) -> _ExecutionOutcome:
        """按 mode 深度严格照单执行计划。"""

        config = self._config
        if config.mode == "plan-only":
            return _ExecutionOutcome(apply_status=ApplyStatus.SKIPPED)

        file_outcome = write_rendered_bundle(bundle, rendered_dir, file_plan=plan.file_plan)
        if config.mode == "write-rendered":
            status = ApplyStatus.SUCCEEDED if not file_outcome.errors else ApplyStatus.FAILED
            return _ExecutionOutcome(apply_status=status, file_outcome=file_outcome)

        # mode == "apply"
        deploy_result = self._adapters.apply_executor.deploy(
            state=state,
            container_plan=plan.container_plan,
        )
        deploy_ok = deploy_result.succeeded and not file_outcome.errors
        if deploy_ok:
            # 容器已按计划部署（定义即真实状态），与收敛是否成功无关：定义记录
            # 落盘以便下次 recreate 给字段级 diff reason。
            persist_container_definitions(paths.container_definitions_dir, plan.container_plan)
        convergence: ConvergenceResult | None = None
        if deploy_ok and config.local_convergence:
            # 收敛（WG_SYNC / loopback / bird reload）执行前，先把本地私钥推进 wg
            # 容器临时目录，脚本据此把 secret:// 占位符替换为真实私钥喂给 wg syncconf。
            wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
            if wg_container is not None:
                push_wireguard_key_to_container(
                    state, paths, container=wg_container, container_exec=self._adapters.container_exec
                )
            convergence = execute_convergence_plan(
                plan.convergence_plan, container_exec=self._adapters.container_exec
            )
        # apply 成功 = 部署成功 且（未跑收敛 或 收敛全部成功）。收敛失败意味着隧道/
        # BGP 没有热加载成功——这次 apply 没有真正达成期望态，必须如实记为 FAILED，
        # 从而不推进 applied_generation、下一轮重试（容器已 KEEP，重试只重放收敛，不抖）。
        succeeded = deploy_ok and (convergence is None or convergence.ok)
        return _ExecutionOutcome(
            apply_status=ApplyStatus.SUCCEEDED if succeeded else ApplyStatus.FAILED,
            file_outcome=file_outcome,
            deploy_result=deploy_result,
            convergence=convergence,
        )

    def _sync_wireguard_keys(self, state: DesiredState, paths: AgentPaths) -> None:
        """兑现本端 WG 私钥并上报公钥+托管密文，触发控制面一致性校验。

        仅在 ``apply`` 模式 + 接入控制面时执行。控制面回 409（公钥与记录冲突）
        时抛 ControllerError 中止本次 reconcile——绝不用偏离的密钥拉起隧道。
        """

        config = self._config
        session = self._adapters.session
        if config.mode != "apply" or session is None:
            return

        recovery = session.call(lambda client: client.fetch_recovery_public_key())
        recovery_pem = recovery.public_key_pem if recovery.configured else None
        report = build_wireguard_key_report(state, paths, recovery_public_pem=recovery_pem)
        if report is None:
            return

        try:
            session.call(lambda client: client.report_wireguard_keys(report))
        except ControllerError as exc:
            if exc.status_code == 409:
                _LOGGER.error(
                    "wireguard 密钥一致性校验失败（控制面 409），中止 reconcile：%s", exc
                )
            raise

    def _build_network_observers(
        self, state: DesiredState, observed: ObservedProject
    ) -> tuple[WireGuardObserver | None, BgpObserver | None]:
        """构造生产路径的 WG / BGP 观察器（容器内 exec 进入路由 netns 采集）。

        没有任何受管容器在场（无 Docker / 全新节点）时直接跳过——此时 exec
        必然失败，空观测让 reconcile 自动忽略这两个维度，不产生假阳性。
        """

        if not observed.containers:
            return None, None

        container_exec = self._adapters.container_exec
        wireguard_observer: WireGuardObserver | None = None
        bgp_observer: BgpObserver | None = None

        wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
        if wg_container is not None:
            wireguard_observer = WireGuardObserver(
                command_runner=container_output_runner(
                    container_exec, wg_container, ["wg", "show", "all", "dump"]
                )
            )

        bird_container = service_container_by_role(state, ServiceRole.BIRD_ROUTER)
        if bird_container is not None:
            # BIRD protocol 名由会话名经同一套规范化函数派生，反查映射两边永远一致。
            name_to_session = {
                bird_protocol_name(session.name): session.name
                for session in state.bgp_sessions
            }
            bgp_observer = BgpObserver(
                command_runner=container_output_runner(
                    container_exec, bird_container, ["birdc", "show", "protocols"]
                ),
                name_to_session=name_to_session,
            )

        return wireguard_observer, bgp_observer

    def _current_identity(self, state: DesiredState, paths: AgentPaths) -> LocalAgentIdentity:
        """取当前权威身份。

        有 Session 时返回其持有的身份——本轮 _publish 若触发 401 自愈会在 Session
        内部轮换并落盘 token，这里取到的就是轮换后的最新身份；离线模式从磁盘加载。
        """

        session = self._adapters.session
        if session is not None:
            return session.ensure()
        return load_identity(paths.identity_file)

    def _persist_identity(self, identity: LocalAgentIdentity, paths: AgentPaths) -> None:
        session = self._adapters.session
        if session is not None:
            session.persist(identity)
        else:
            save_identity(identity, paths.identity_file)

    def _publish(
        self,
        state: DesiredState,
        plan: ReconcilePlan,
        execution: _ExecutionOutcome,
        snapshot: RuntimeSnapshot,
        report: ReconciliationReport,
    ) -> dict[str, Any]:
        """经 Session 上报观察结果；非控制面模式为空操作。"""

        session = self._adapters.session
        if session is None:
            return {}

        controller_acks: dict[str, Any] = {}
        registration_ack = session.take_registration_ack()
        if registration_ack is not None:
            controller_acks["registration"] = registration_ack

        apply_result = ApplyResult(
            node_id=state.node.node_id,
            generation=state.generation,
            status=execution.apply_status,
            started_at=report.captured_at,
            finished_at=report.captured_at,
            plan_summary=plan.file_plan.summary,
            applied_files=_applied_files(plan, execution),
            errors=list(execution.file_outcome.errors) if execution.file_outcome else [],
        )
        controller_acks["runtime_snapshot"] = session.call(
            lambda client: client.post_runtime_snapshot(snapshot)
        )
        controller_acks["reconciliation_report"] = session.call(
            lambda client: client.post_reconciliation_report(report)
        )
        controller_acks["apply_result"] = session.call(
            lambda client: client.post_apply_result(apply_result)
        )
        return controller_acks


def _applied_files(plan: ReconcilePlan, execution: _ExecutionOutcome) -> list[AppliedFileRecord]:
    """上报的文件动作与计划/执行同源。

    实际执行过（write-rendered / apply）时取真实执行结果；plan-only 取计划
    本身（"将会发生什么"）。两者结构一致，非 noop 才上报。
    """

    if execution.file_outcome is not None:
        return [
            AppliedFileRecord(action=item.action, path=item.path, sha256=item.sha256)
            for item in execution.file_outcome.applied
            if item.action != "noop"
        ]
    return [
        AppliedFileRecord(
            action=action.action,
            path=action.path,
            sha256=action.desired_sha256 or action.observed_sha256,
        )
        for action in plan.file_plan.actions
        if action.action != "noop"
    ]


class _StaticObserver(DockerObserver):
    """把已采集结果原样返回的 stub observer，避免重复调用 Docker。"""

    def __init__(self, observed: ObservedProject) -> None:
        super().__init__(docker_factory=lambda: None)
        self._observed = observed

    def observe_project(self, state: DesiredState) -> ObservedProject:  # noqa: D401 - simple stub
        return self._observed


def run_once(config: AgentConfig, adapters: Adapters | None = None) -> OrchestratorResult:
    """便捷入口：执行一次 reconcile。

    未传 `adapters` 时按配置自建生产装配，并在本次运行结束后释放；
    常驻进程应自建并复用 `Adapters`（HTTP 连接池跨 reconcile 复用）。
    """

    owns_adapters = adapters is None
    if adapters is None:
        adapters = Adapters.build(config)
    try:
        return ReconcileOrchestrator(config=config, adapters=adapters).run_once()
    finally:
        if owns_adapters:
            adapters.close()


__all__ = [
    "OrchestratorResult",
    "ReconcileOrchestrator",
    "run_once",
]
