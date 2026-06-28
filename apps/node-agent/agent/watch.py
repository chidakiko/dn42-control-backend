from __future__ import annotations

"""Agent 常驻监听模式：client 与 runtime 解耦的两任务结构。

与单次 ``run_once`` 不同，``run_watch`` 让 agent 变成一个常驻进程，内部由
两个职责单一的任务协作，中间用"门铃"（latest-wins 脏标志）衔接：

- **client 任务**（:func:`_client_loop`）：只负责连接控制面的私有 WebSocket
  通道 ``/api/v1/agent/ws/{node_id}``、断线指数退避重连、读取事件并按响门铃。
  它从不执行 reconcile，因此无论收敛跑多久，WS 都被即时消费——控制面
  EventBus 的发送队列不会因 agent 忙碌而背压堆积丢事件。
- **consumer 任务**（:func:`_consumer_loop`）：系统里**唯一**的 reconcile
  入口，串行消费门铃。门铃事件不携带业务数据（level-triggered），每次
  reconcile 都拉取最新全量状态，因此任意多声门铃合并成一次收敛即可：
  reconcile 运行期间到达的事件只是把门铃再按响，结束后恰好补一次。
  长时间无门铃时按 ``fallback_interval`` 兜底收敛——该兜底独立于 WS
  连接状态，即使首次注册失败（控制面暂不可达）也会持续重试。

事件源通过 ``event_source`` 依赖注入，单测可传入假事件源，完全不依赖真实网络。
"""

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

from .adapters import Adapters
from .core.config import AgentConfig
from .core.identity import LocalAgentIdentity, load_identity
from .core.paths import AgentPaths
from .metrics import record_reconcile, record_self_observation
from .observability import CpuSampler, current_rss_mb, warn_if_slow
from .orchestrator import OrchestratorResult, run_once
from .reresolve import reresolve_and_report
from .l3_heal import heal_l3_drift, HealCircuit
from .routing import collect_and_publish_routing
from .traffic import collect_and_publish_traffic

logger = logging.getLogger(__name__)

ReconcileFn = Callable[[AgentConfig], OrchestratorResult]

# 读取"本地已应用到哪一代"，供事件去重 / hello 追赶判定。
AppliedGenerationReader = Callable[[], int | None]


class EventSource(Protocol):
    """门铃事件源：进入后持续产出事件 dict，断开时迭代结束或抛错。

    实现需是一个异步上下文管理器，``__aiter__`` 返回事件流。
    """

    def __call__(self, ws_url: str, token: str) -> "EventStream":
        ...


class EventStream(Protocol):
    async def __aenter__(self) -> AsyncIterator[dict]:
        ...

    async def __aexit__(self, *exc_info: object) -> bool | None:
        ...


@dataclass
class _Doorbell:
    """client 与 consumer 之间的 latest-wins 门铃。

    门铃事件是幂等触发器（"状态变了，去拉最新的"），不是数据载体——
    所以这里不需要 FIFO 队列：积压 N 声门铃与 1 声等价，重复 ``ring``
    天然合并。``reasons`` 只为日志可读性记录触发原因集合。
    """

    reasons: set[str] = field(default_factory=set)
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def ring(self, reason: str) -> None:
        self.reasons.add(reason)
        self.wake.set()

    def drain(self) -> set[str]:
        """取走当前全部原因并复位门铃。"""

        reasons = set(self.reasons)
        self.reasons.clear()
        self.wake.clear()
        return reasons


def resolve_node_id(config: AgentConfig) -> str | None:
    """确定本地 node_id：显式 ``requested_node_id`` 优先，否则扫描唯一节点目录。

    无法确定（未注册 / 多节点目录歧义）时返回 ``None``，由调用方决定如何处理。
    """

    node_id = config.requested_node_id
    if node_id is not None:
        return node_id
    nodes_dir = config.state_dir / "nodes"
    if nodes_dir.is_dir():
        candidates = [p.name for p in nodes_dir.iterdir() if p.is_dir()]
        if len(candidates) == 1:
            return candidates[0]
    return None


def _resolve_identity(config: AgentConfig) -> LocalAgentIdentity:
    """从本地状态目录加载身份，用于拿 node_id + token 建 WS 连接。"""

    node_id = resolve_node_id(config)
    if node_id is None:
        raise RuntimeError(
            "watch 模式需要确定 node_id：请用 --requested-node-id 指定，或先成功注册一次"
        )
    paths = AgentPaths(config.state_dir, node_id)
    return load_identity(paths.identity_file)


def _record_reconcile_metrics(
    config: AgentConfig,
    result: OrchestratorResult | None,
    duration_seconds: float,
    *,
    failed: bool,
) -> None:
    """把一次 reconcile 的结果累计写入节点指标文件。

    有真实结果时取其 ``apply_status`` 与 node_id / generation；reconcile 抛异常
    （``failed``）时按 "failed" 记一笔，node_id 退回配置推断。指标写入失败只记
    debug，绝不影响常驻循环。
    """

    status: str | None = None
    node_id: str | None = None
    generation: int | None = None
    if result is not None and hasattr(result, "state"):
        node_id = result.state.node.node_id
        status = result.apply_status.value
        generation = result.state.generation
    elif failed:
        node_id = resolve_node_id(config)
        status = "failed"
    else:
        # 没有可记录的结果（例如单测注入的桩返回 None 且未抛错）。
        return

    if node_id is None or status is None:
        return
    try:
        record_reconcile(
            AgentPaths(config.state_dir, node_id).metrics_file,
            status=status,
            duration_seconds=duration_seconds,
            generation=generation,
        )
    except Exception:  # noqa: BLE001 - 指标是尽力而为，绝不阻断 reconcile
        logger.debug("watch: 记录 reconcile 指标失败", exc_info=True)


def _record_self(config: AgentConfig, node_id: str, **fields: float | None) -> None:
    """把背景循环耗时 / 进程自观测落到节点 metrics 文件，best-effort（绝不阻断循环）。"""

    try:
        record_self_observation(AgentPaths(config.state_dir, node_id).metrics_file, **fields)
    except Exception:  # noqa: BLE001 - 自观测是尽力而为
        logger.debug("watch: 记录自观测指标失败", exc_info=True)


async def _self_monitor_loop(
    *,
    config: AgentConfig,
    interval: float,
    cpu_warn_percent: float,
    stop: asyncio.Event,
) -> None:
    """周期采集 agent **自身** CPU%/RSS 写入 metrics 文件，并对持续高 CPU 告警。

    与背景循环各自上报的耗时合起来，让"agent 忙不忙、哪个循环慢了"在 metrics 文件 /
    ``doctor`` 里一眼可见——排障不必再临时往生产装 py-spy。CPU 取进程级（含全部线程），
    所以即便热点在 executor 工作线程里也能捕获。
    """

    sampler = CpuSampler()
    # 先睡一个间隔再首采：第一段窗口才有 CPU 区间可算。
    await _sleep_or_stop(stop, interval)
    while not stop.is_set():
        node_id = resolve_node_id(config)
        if node_id is not None:
            cpu_percent, _ = sampler.sample()
            _record_self(config, node_id, cpu_percent=cpu_percent, rss_mb=current_rss_mb())
            if cpu_warn_percent > 0 and cpu_percent >= cpu_warn_percent:
                logger.warning(
                    "observability: agent 自身 CPU %.0f%% 持续偏高（阈值 %.0f%%，含全部"
                    "线程）——疑似背景循环热点，建议排查",
                    cpu_percent,
                    cpu_warn_percent,
                )
        await _sleep_or_stop(stop, interval)


def build_ws_url(controller_url: str, node_id: str) -> str:
    """把 controller 的 http(s) base URL 转成节点私有 WS URL。"""

    parts = urlsplit(controller_url.rstrip("/"))
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    path = f"{parts.path}/api/v1/agent/ws/{node_id}"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def _default_event_source(ws_url: str, token: str) -> "EventStream":
    """基于 ``websockets`` 的默认事件源。"""

    import json

    from websockets.asyncio.client import connect

    class _Stream:
        async def __aenter__(self) -> AsyncIterator[dict]:
            # connect() 是 websockets 的强类型 async CM；这里只做透明委托，
            # 用 Any 句柄避免把 __aexit__ 的 *exc_info: object 往强类型签名上硬塞。
            self._conn: Any = connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {token}"},
            )
            self._ws = await self._conn.__aenter__()
            return self._iterate()

        async def __aexit__(self, *exc_info: object) -> bool | None:
            return await self._conn.__aexit__(*exc_info)

        async def _iterate(self) -> AsyncIterator[dict]:
            async for raw in self._ws:
                try:
                    yield json.loads(raw)
                except (ValueError, TypeError):
                    logger.warning("丢弃无法解析的 ws 消息: %r", raw)

    return _Stream()


async def run_watch(
    config: AgentConfig,
    *,
    reconcile: ReconcileFn = run_once,
    event_source: EventSource = _default_event_source,
    initial_backoff: float = 1.0,
    max_backoff: float = 30.0,
    fallback_interval: float = 300.0,
    debounce_seconds: float = 0.3,
    routing_interval: float | None = None,
    traffic_interval: float | None = None,
    reresolve_interval: float | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """启动常驻监听循环，直到 ``stop_event`` 被设置。

    Args:
        config: agent 配置（必须含 controller_url）。
        reconcile: 触发一次 reconcile 的函数，默认 ``run_once``；单测可注入桩。
        event_source: WS 事件源工厂，默认基于 ``websockets``；单测可注入假源。
        initial_backoff: 首次重连等待秒数。
        max_backoff: 重连退避上限秒数。
        fallback_interval: 长时间无门铃时的兜底 reconcile 间隔秒数。
        debounce_seconds: 门铃响后的静等窗口，把突发的连续变更（批量 admin
            写入）合并成一次 reconcile；reconcile 运行期间的门铃则由
            latest-wins 语义天然合并。设 0 关闭窗口。
        routing_interval: 路由全表周期采集间隔秒数；``None`` 取配置值，``<=0``
            关闭。仅在默认 reconcile（拥有共享 Adapters）时启用——它是独立于
            reconcile 的纯观测任务，绝不触碰收敛路径。
        traffic_interval: WG 流量 30s 轻量采集间隔秒数；``None`` 取配置值，``<=0``
            关闭。同为独立旁路观测任务（只跑 ``wg show all transfer`` 求和上报），
            仅在默认 reconcile（持有共享 Adapters）时启用。
        reresolve_interval: WG endpoint 周期重解析间隔秒数；``None`` 取配置值，
            ``<=0`` 关闭。同样仅在默认 reconcile（持有共享 Adapters）时启用——
            它是独立于 reconcile 的自愈任务，只在握手超时时 ``wg set`` 重设 endpoint。
        stop_event: 设置后优雅退出循环。
    """

    if config.controller_url is None:
        raise RuntimeError("watch 模式需要 --controller-url")

    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    bell = _Doorbell()

    # 默认 reconcile 走共享 Adapters：HTTP 连接池等长生命周期资源在守护
    # 进程内装配一次、跨 reconcile 复用，退出时统一释放。
    adapters: Adapters | None = None
    if reconcile is run_once:
        adapters = Adapters.build(config)
        shared = adapters

        def _shared_reconcile(cfg: AgentConfig) -> OrchestratorResult:
            return run_once(cfg, shared)

        reconcile = _shared_reconcile

    async def _reconcile(reason: str) -> None:
        logger.info("watch: 触发 reconcile (%s)", reason)
        started = time.monotonic()
        result: OrchestratorResult | None = None
        failed = False
        try:
            result = await loop.run_in_executor(None, reconcile, config)
        except Exception:  # noqa: BLE001 - 单次失败不应拖垮常驻循环
            failed = True
            logger.exception("watch: reconcile 失败 (%s)", reason)
        finally:
            _record_reconcile_metrics(config, result, time.monotonic() - started, failed=failed)

    # 启动先 reconcile 一次，确保身份就绪、节点拉到期望态。
    await _reconcile("startup")

    client_task = asyncio.create_task(
        _client_loop(
            config=config,
            event_source=event_source,
            bell=bell,
            initial_backoff=initial_backoff,
            max_backoff=max_backoff,
            stop=stop,
        ),
        name="dn42-agent-ws-client",
    )

    # 路由全表采集是独立旁路任务：只有默认 reconcile（持有共享 Adapters）时启用，
    # 注入桩 reconcile 的单测不会无意触发真实容器采集。
    effective_routing_interval = (
        routing_interval if routing_interval is not None else config.routing_interval_seconds
    )
    routing_task: asyncio.Task[None] | None = None
    if adapters is not None and effective_routing_interval > 0:
        routing_task = asyncio.create_task(
            _routing_loop(
                config=config,
                adapters=adapters,
                interval=effective_routing_interval,
                stop=stop,
            ),
            name="dn42-agent-routing",
        )

    # WG 流量 30s 轻量采集同为独立旁路任务：只有默认 reconcile（持有共享 Adapters）时启用。
    effective_traffic_interval = (
        traffic_interval if traffic_interval is not None else config.traffic_interval_seconds
    )
    traffic_task: asyncio.Task[None] | None = None
    if adapters is not None and effective_traffic_interval > 0:
        traffic_task = asyncio.create_task(
            _traffic_loop(
                config=config,
                adapters=adapters,
                interval=effective_traffic_interval,
                stop=stop,
            ),
            name="dn42-agent-traffic",
        )

    # WG endpoint 重解析同为独立旁路任务：只有默认 reconcile（持有共享 Adapters）时启用。
    effective_reresolve_interval = (
        reresolve_interval if reresolve_interval is not None else config.reresolve_interval_seconds
    )
    reresolve_task: asyncio.Task[None] | None = None
    if adapters is not None and effective_reresolve_interval > 0:
        reresolve_task = asyncio.create_task(
            _reresolve_loop(
                config=config,
                adapters=adapters,
                interval=effective_reresolve_interval,
                stop=stop,
            ),
            name="dn42-agent-reresolve",
        )

    # L3 漂移自愈同为独立旁路任务：周期比对期望接口 vs netns 实况，缺失即重 apply 补齐。
    l3_heal_task: asyncio.Task[None] | None = None
    if adapters is not None and config.l3_heal_interval_seconds > 0:
        l3_heal_task = asyncio.create_task(
            _l3_heal_loop(
                config=config,
                adapters=adapters,
                interval=config.l3_heal_interval_seconds,
                stop=stop,
            ),
            name="dn42-agent-l3-heal",
        )

    # 进程自观测（CPU/RSS + 高 CPU 告警）：纯本地、不依赖 adapters，但同样仅在常驻
    # 守护进程下启用，避免注入桩的单测无意写指标文件。设 0 关闭。
    self_monitor_task: asyncio.Task[None] | None = None
    if adapters is not None and config.self_monitor_interval_seconds > 0:
        self_monitor_task = asyncio.create_task(
            _self_monitor_loop(
                config=config,
                interval=config.self_monitor_interval_seconds,
                cpu_warn_percent=config.cpu_warn_percent,
                stop=stop,
            ),
            name="dn42-agent-self-monitor",
        )

    try:
        await _consumer_loop(
            bell=bell,
            reconcile=_reconcile,
            fallback_interval=fallback_interval,
            debounce_seconds=debounce_seconds,
            stop=stop,
        )
    finally:
        client_task.cancel()
        if routing_task is not None:
            routing_task.cancel()
        if traffic_task is not None:
            traffic_task.cancel()
        if reresolve_task is not None:
            reresolve_task.cancel()
        if l3_heal_task is not None:
            l3_heal_task.cancel()
        if self_monitor_task is not None:
            self_monitor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await client_task
        if routing_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await routing_task
        if traffic_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await traffic_task
        if reresolve_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reresolve_task
        if l3_heal_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await l3_heal_task
        if self_monitor_task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self_monitor_task
        if adapters is not None:
            adapters.close()


async def _client_loop(
    *,
    config: AgentConfig,
    event_source: EventSource,
    bell: _Doorbell,
    initial_backoff: float,
    max_backoff: float,
    stop: asyncio.Event,
) -> None:
    """维护 WS 长连接：解析身份、连接、读事件按门铃、断线退避重连。"""

    assert config.controller_url is not None  # run_watch 已校验
    backoff = initial_backoff
    while not stop.is_set():
        try:
            identity = _resolve_identity(config)
        except RuntimeError as exc:
            # node_id 无法确定（未注册成功 / 多节点目录歧义）：等兜底 reconcile
            # 完成注册后这里自然恢复，期间持续记错并退避，不杀死 client 任务。
            logger.error("watch: %s，%.0fs 后重试", exc, backoff)
            await _sleep_or_stop(stop, backoff)
            backoff = min(backoff * 2, max_backoff)
            continue
        if identity.node_id is None or identity.agent_token is None:
            logger.error("watch: 本地身份缺失 node_id/token，%.0fs 后重试", backoff)
            await _sleep_or_stop(stop, backoff)
            backoff = min(backoff * 2, max_backoff)
            continue

        ws_url = build_ws_url(config.controller_url, identity.node_id)
        applied_reader = _applied_generation_reader(config, identity.node_id)
        try:
            logger.info("watch: 连接 %s", ws_url)
            await _pump_events(
                event_source=event_source,
                ws_url=ws_url,
                token=identity.agent_token,
                bell=bell,
                applied_generation=applied_reader,
                stop=stop,
            )
            backoff = initial_backoff  # 正常断开后重置退避
            logger.info("watch: ws 事件流结束")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络类异常统一退避重连
            logger.warning("watch: 连接异常 %s，%.0fs 后重连", exc, backoff)
            await _sleep_or_stop(stop, backoff)
            backoff = min(backoff * 2, max_backoff)


def _applied_generation_reader(config: AgentConfig, node_id: str) -> AppliedGenerationReader:
    """每次读取磁盘上的 identity，拿到最新的 applied_generation。

    身份文件是 reconcile 与 watch 之间的共享状态载体；事件稀疏，按需读盘
    的成本可忽略，换来去重判定永远基于最新事实。
    """

    identity_file = AgentPaths(config.state_dir, node_id).identity_file

    def read() -> int | None:
        return load_identity(identity_file).applied_generation

    return read


async def _pump_events(
    *,
    event_source: EventSource,
    ws_url: str,
    token: str,
    bell: _Doorbell,
    applied_generation: AppliedGenerationReader,
    stop: asyncio.Event,
) -> None:
    """消费一条 WS 连接上的事件流，直到流结束或 ``stop`` 被设置。

    本函数只读不算：把门铃事件转成 ``bell.ring``，立即回到 recv——
    保证服务端发送队列永远被及时排空。判定规则（保守去重，宁多勿漏）：

    - ``hello``：携带控制面当前 generation。本地落后即按门铃**追赶**——
      断线期间漏掉的变更在重连瞬间补偿，而不是等兜底周期；
    - ``desired_state_updated``：generation 不高于本地已应用值时去重跳过
      （事件是幂等门铃，旧代/重复推送无需响应）；缺 generation 一律响铃；
    - ``snapshot_request``：永远响铃（控制面要的是新鲜快照）。
    """

    stream = event_source(ws_url, token)
    async with stream as events:
        async for event in events:
            _dispatch_event(event, bell, applied_generation)
            if stop.is_set():
                return


def _dispatch_event(
    event: object,
    bell: _Doorbell,
    applied_generation: AppliedGenerationReader,
) -> None:
    if not isinstance(event, dict):
        logger.debug("watch: 忽略非字典事件 %r", event)
        return
    event_type = event.get("type")
    if event_type == "snapshot_request":
        _log_event(event)
        bell.ring(event_type)
        return

    if event_type == "desired_state_updated":
        generation = event.get("generation")
        applied = applied_generation()
        if isinstance(generation, int) and applied is not None and generation <= applied:
            logger.info(
                "watch: 事件 generation=%s 已应用（本地 %s），去重跳过", generation, applied
            )
            return
        _log_event(event)
        bell.ring(event_type)
        return

    if event_type == "hello":
        generation = event.get("generation")
        applied = applied_generation()
        if isinstance(generation, int) and (applied is None or generation > applied):
            logger.info(
                "watch: hello 显示控制面 generation=%s 领先本地 %s，追赶", generation, applied
            )
            bell.ring("hello-catchup")
        else:
            logger.debug("watch: hello generation=%s 与本地一致，无需追赶", generation)
        return

    logger.debug("watch: 忽略事件 %s", event_type)


async def _consumer_loop(
    *,
    bell: _Doorbell,
    reconcile: Callable[[str], Awaitable[None]],
    fallback_interval: float,
    debounce_seconds: float,
    stop: asyncio.Event,
) -> None:
    """串行消费门铃：唯一的 reconcile 入口。

    等待门铃或 ``fallback_interval`` 超时；门铃响后静等 ``debounce_seconds``
    合并突发，然后一次性 drain 全部原因执行 reconcile。退出前若门铃仍响着
    （stop 与最后一声门铃同时到达），先收敛再退出，绝不丢收敛。
    """

    while not stop.is_set():
        try:
            await asyncio.wait_for(_wake_or_stop(bell, stop), timeout=fallback_interval)
        except asyncio.TimeoutError:
            await reconcile("fallback-interval")
            continue

        if bell.wake.is_set():
            if debounce_seconds > 0:
                await _sleep_or_stop(stop, debounce_seconds)
            reasons = bell.drain()
            await reconcile("event:" + "+".join(sorted(reasons)))
        # stop 在无门铃时被设置 → 直接走循环条件退出。

    # stop 与门铃竞争时不漏最后一次收敛。
    if bell.wake.is_set():
        reasons = bell.drain()
        await reconcile("event:" + "+".join(sorted(reasons)))


async def _routing_loop(
    *,
    config: AgentConfig,
    adapters: Adapters,
    interval: float,
    stop: asyncio.Event,
) -> None:
    """周期采集并上报 BIRD 路由全表，直到 ``stop`` 被设置。

    与 consumer 的 reconcile 完全隔离：采集走 ``container_exec`` 只读、上报走
    Session HTTP，绝不触发 apply。node_id 每轮重解析——首次注册完成前解析不到
    时跳过本轮，注册成功后自然开始采集。单次失败只记日志，循环继续。

    启动后先采一次（仅留出很短的 grace 让启动 reconcile 把 desired-state 落盘 /
    容器拉起），而不是干等满一个 interval——否则默认 5 分钟才出现第一帧路由，
    重启后会显得"没生效"。之后按 ``interval`` 周期采集。
    """

    loop = asyncio.get_running_loop()

    async def _collect_once() -> None:
        node_id = resolve_node_id(config)
        if node_id is None:
            logger.debug("routing: 尚不能确定 node_id，跳过本轮路由采集")
            return
        started = time.monotonic()
        try:
            await loop.run_in_executor(
                None, collect_and_publish_routing, config, adapters, node_id
            )
        except Exception:  # noqa: BLE001 - 路由采集是旁路观测，绝不拖垮守护进程
            logger.warning("routing: 路由全表采集/上报失败", exc_info=True)
            return
        duration = time.monotonic() - started
        # 单轮采集 > 整个间隔 = 追不上节奏（RPKI O(路由×ROA) 爆炸那类回归的特征），自动告警。
        warn_if_slow("routing-collect", duration, interval)
        _record_self(config, node_id, routing_collect_seconds=duration)

    # 启动 grace：给首次 reconcile 落盘窗口，但远小于 interval，确保重启后很快出首帧。
    await _sleep_or_stop(stop, min(interval, 10.0))
    while not stop.is_set():
        await _collect_once()
        await _sleep_or_stop(stop, interval)


async def _traffic_loop(
    *,
    config: AgentConfig,
    adapters: Adapters,
    interval: float,
    stop: asyncio.Event,
) -> None:
    """周期采集并上报 WG 流量累计计数（30s 轻量），直到 ``stop`` 被设置。

    与 consumer 的 reconcile 完全隔离：只跑一次 ``wg show all transfer`` 求和 + Session
    HTTP 上报，绝不触发 apply。node_id 每轮重解析——首次注册完成前解析不到时跳过本轮。
    单次失败只记日志，循环继续。启动 grace 较短：让吞吐曲线重启后很快出首帧。
    """

    loop = asyncio.get_running_loop()

    async def _collect_once() -> None:
        node_id = resolve_node_id(config)
        if node_id is None:
            logger.debug("traffic: 尚不能确定 node_id，跳过本轮流量采集")
            return
        started = time.monotonic()
        try:
            await loop.run_in_executor(
                None, collect_and_publish_traffic, config, adapters, node_id
            )
        except Exception:  # noqa: BLE001 - 流量采集是旁路观测，绝不拖垮守护进程
            logger.warning("traffic: WG 流量采集/上报失败", exc_info=True)
            return
        warn_if_slow("traffic-collect", time.monotonic() - started, interval)

    await _sleep_or_stop(stop, min(interval, 10.0))
    while not stop.is_set():
        await _collect_once()
        await _sleep_or_stop(stop, interval)


async def _reresolve_loop(
    *,
    config: AgentConfig,
    adapters: Adapters,
    interval: float,
    stop: asyncio.Event,
) -> None:
    """周期检查 WG 域名 endpoint 的握手，超时即重设 endpoint，直到 ``stop``。

    与 consumer 的 reconcile 完全隔离：只读握手 + ``wg set`` 热更新 endpoint（解析到
    同一 IP 即无扰动），从不重建容器、不触碰 ``applied_generation``。node_id 每轮重解析——
    首次注册完成前解析不到时跳过本轮。单次失败只记日志，循环继续。

    启动 grace 略长于路由任务：给首次 reconcile 把 desired-state 落盘 + 拉起 wg 容器
    的窗口，避免重启瞬间空跑一轮。
    """

    loop = asyncio.get_running_loop()

    async def _reresolve_once() -> None:
        node_id = resolve_node_id(config)
        if node_id is None:
            logger.debug("reresolve: 尚不能确定 node_id，跳过本轮")
            return
        started = time.monotonic()
        try:
            await loop.run_in_executor(None, reresolve_and_report, config, adapters, node_id)
        except Exception:  # noqa: BLE001 - 自愈是旁路，绝不拖垮守护进程
            logger.warning("reresolve: 本轮重解析失败", exc_info=True)
            return
        duration = time.monotonic() - started
        warn_if_slow("reresolve", duration, interval)
        _record_self(config, node_id, reresolve_seconds=duration)

    await _sleep_or_stop(stop, min(interval, 15.0))
    while not stop.is_set():
        await _reresolve_once()
        await _sleep_or_stop(stop, interval)


async def _l3_heal_loop(
    *,
    config: AgentConfig,
    adapters: Adapters,
    interval: float,
    stop: asyncio.Event,
) -> None:
    """周期比对期望接口 vs netns 实际存在的接口，缺失即重跑 apply-all-wg 补齐，直到 ``stop``。

    与 consumer 的 reconcile 完全隔离的运行时纠偏：``ip -br link`` 探测 + ``apply-all-wg``
    幂等重放，从不重建容器、不触碰 ``applied_generation``。node_id 每轮重解析；单次失败
    只记日志、循环继续。启动 grace 给首次 reconcile 落盘 desired-state + 拉起 wg 容器的窗口。
    """

    loop = asyncio.get_running_loop()
    circuit = HealCircuit(interval)

    async def _heal_once() -> bool:
        """返回 ``False`` 仅当「检测到漂移但没补好」（触发退避/熔断）；其余
        （补好 / 无漂移 / 探针不可达 / 身份未就绪）均视作成功，不计失败。"""

        node_id = resolve_node_id(config)
        if node_id is None:
            logger.debug("l3-heal: 尚不能确定 node_id，跳过本轮")
            return True
        started = time.monotonic()
        try:
            result = await loop.run_in_executor(None, heal_l3_drift, config, adapters, node_id)
        except Exception:  # noqa: BLE001 - 自愈是旁路，绝不拖垮守护进程
            logger.warning("l3-heal: 本轮自愈失败", exc_info=True)
            return False
        warn_if_slow("l3-heal", time.monotonic() - started, interval)
        if result is None:
            return True  # 无漂移 / 探针不可达 —— 非失败
        return bool(result.get("healed"))

    await _sleep_or_stop(stop, min(interval, 15.0))
    while not stop.is_set():
        signal = circuit.record(await _heal_once())
        if signal == "escalate":
            logger.error(
                "l3-heal: 连续 %d 次补救失败，熔断——改长间隔半开重试；疑似底层故障"
                "（接口被持续删除 / apply 一直失败），需人工介入",
                circuit.failures,
            )
        elif signal == "recovered":
            logger.info("l3-heal: 补救恢复，熔断复位")
        await _sleep_or_stop(stop, circuit.backoff())


async def _wake_or_stop(bell: _Doorbell, stop: asyncio.Event) -> None:
    """等待门铃或 stop 任一发生；保证子 future 被干净回收。"""

    waiters = {
        asyncio.ensure_future(bell.wake.wait()),
        asyncio.ensure_future(stop.wait()),
    }
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            if not waiter.done():
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter


def _log_event(event: dict) -> None:
    """记录门铃事件与控制面附带的变更原因（仅供排错，不参与收敛判定）。"""

    logger.info(
        "watch: 收到事件 type=%s generation=%s reason=%s",
        event.get("type"),
        event.get("generation"),
        event.get("reason"),
    )


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """睡 ``seconds`` 秒，但若 ``stop`` 期间被设置则提前返回。"""

    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)


__all__ = ["run_watch", "build_ws_url", "EventSource", "EventStream"]
