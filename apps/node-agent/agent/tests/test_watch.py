from __future__ import annotations

"""节点 agent 常驻监听 (``run_watch``) 的单元测试。

watch 模式是 client（WS 长连接）与 consumer（唯一 reconcile 入口）的两任务
结构，中间用 latest-wins 门铃衔接。本文件用注入的假事件源完全规避真实网络，
锁定以下行为：

* 启动时无条件 reconcile 一次（startup）；
* 门铃事件触发 reconcile，``hello`` 等非门铃事件不触发；突发门铃合并成一次；
* **reconcile 运行期间**到达的门铃由 client 即时消费、合并为恰好一次补偿
  reconcile——不丢、不放大；
* 连接失败按退避重连，事件流结束后正常重连；
* ``stop_event`` 被设置后循环优雅退出，与最后一声门铃竞争时不丢收敛；
* ``build_ws_url`` 能把 http(s) base URL 正确转成节点私有 ws(s) 路径。
"""

import asyncio
import threading
from pathlib import Path

import pytest

from agent.core.config import AgentConfig
from agent.core.identity import LocalAgentIdentity, save_identity
from agent.core.paths import AgentPaths
from agent.watch import build_ws_url, run_watch


def _make_config(tmp_path: Path) -> AgentConfig:
    return AgentConfig(
        controller_url="http://controller.example:8000",
        requested_node_id="edge1",
        state_dir=tmp_path,
    )


def _seed_identity(config: AgentConfig) -> None:
    paths = AgentPaths(config.state_dir, "edge1")
    paths.node_dir.mkdir(parents=True, exist_ok=True)
    save_identity(
        LocalAgentIdentity(node_id="edge1", agent_id="a", agent_token="tok"),
        paths.identity_file,
    )


def test_build_ws_url_maps_scheme_and_path() -> None:
    assert (
        build_ws_url("http://controller.example:8000", "edge1")
        == "ws://controller.example:8000/api/v1/agent/ws/edge1"
    )
    assert (
        build_ws_url("https://ctrl/", "n1")
        == "wss://ctrl/api/v1/agent/ws/n1"
    )


@pytest.mark.asyncio
async def test_run_watch_reconciles_on_events(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    _seed_identity(config)

    reasons: list[str] = []
    stop = asyncio.Event()

    def fake_reconcile(_config: AgentConfig):
        # run_in_executor 调用，记录就好
        reasons.append("reconcile")
        return None  # type: ignore[return-value]

    events_to_emit = [
        {"type": "hello", "node_id": "edge1", "generation": 1},
        {"type": "desired_state_updated", "generation": 2},
        {"type": "snapshot_request", "reason": "x"},
    ]

    captured: dict[str, str] = {}

    def fake_event_source(ws_url: str, token: str):
        captured["url"] = ws_url
        captured["token"] = token

        class _Stream:
            async def __aenter__(self):
                return self._gen()

            async def __aexit__(self, *exc):
                return False

            async def _gen(self):
                for ev in events_to_emit:
                    yield ev
                # 事件耗尽后让循环停止，避免无限重连
                stop.set()

        return _Stream()

    await asyncio.wait_for(
        run_watch(
            config,
            reconcile=fake_reconcile,  # type: ignore[arg-type]
            event_source=fake_event_source,
            stop_event=stop,
        ),
        timeout=5,
    )

    # startup 1 次 + 背靠背的两个门铃事件被防抖窗口合并为 1 次 = 2。
    # 每次 reconcile 都拉最新全量状态，突发事件逐个响应只是浪费。
    assert reasons == ["reconcile", "reconcile"]
    assert captured["url"] == "ws://controller.example:8000/api/v1/agent/ws/edge1"
    assert captured["token"] == "tok"


@pytest.mark.asyncio
async def test_run_watch_stops_when_event_set_before_events(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    _seed_identity(config)

    reasons: list[str] = []
    stop = asyncio.Event()

    def fake_reconcile(_config: AgentConfig):
        reasons.append("reconcile")
        # 第一次 startup reconcile 后立即请求停止
        stop.set()
        return None  # type: ignore[return-value]

    def fake_event_source(ws_url: str, token: str):
        class _Stream:
            async def __aenter__(self):
                return self._gen()

            async def __aexit__(self, *exc):
                return False

            async def _gen(self):
                # 永不产出事件；依赖 stop_event 退出
                await asyncio.sleep(3600)
                if False:  # pragma: no cover
                    yield {}

        return _Stream()

    await asyncio.wait_for(
        run_watch(
            config,
            reconcile=fake_reconcile,  # type: ignore[arg-type]
            event_source=fake_event_source,
            stop_event=stop,
        ),
        timeout=5,
    )

    assert reasons == ["reconcile"]  # 只有 startup


@pytest.mark.asyncio
async def test_doorbells_during_reconcile_merge_into_one_catchup(tmp_path: Path) -> None:
    """reconcile 运行期间的门铃必须被 client 即时消费并合并为一次补偿收敛。

    这是 client/consumer 解耦的核心收益回归锁：老实现里监听协程在 reconcile
    期间被阻塞，事件只能积压在 WS 缓冲里；新实现里 client 持续读流按门铃，
    consumer 结束当前 reconcile 后恰好补一次（N 声门铃 → 1 次收敛）。
    """

    config = _make_config(tmp_path)
    _seed_identity(config)

    calls: list[str] = []
    stop = asyncio.Event()
    release = threading.Event()

    def fake_reconcile(_config: AgentConfig):
        calls.append("reconcile")
        if len(calls) == 2:
            # 第一次由事件触发的 reconcile 故意阻塞，等事件源放行。
            release.wait(timeout=5)
        return None  # type: ignore[return-value]

    def fake_event_source(ws_url: str, token: str):
        class _Stream:
            async def __aenter__(self):
                return self._gen()

            async def __aexit__(self, *exc):
                return False

            async def _gen(self):
                yield {"type": "desired_state_updated", "generation": 2}
                # 等 consumer 进入被阻塞的 reconcile……
                while len(calls) < 2:
                    await asyncio.sleep(0.01)
                # ……期间再推两声门铃：client 必须仍在消费（reconcile 没挡住它）。
                yield {"type": "desired_state_updated", "generation": 3}
                yield {"type": "snapshot_request", "reason": "operator"}
                release.set()
                # 等补偿 reconcile 发生后停止。
                while len(calls) < 3:
                    await asyncio.sleep(0.01)
                stop.set()

        return _Stream()

    await asyncio.wait_for(
        run_watch(
            config,
            reconcile=fake_reconcile,  # type: ignore[arg-type]
            event_source=fake_event_source,
            debounce_seconds=0,
            stop_event=stop,
        ),
        timeout=10,
    )

    # startup(1) + 首个事件(1) + 阻塞期间两声门铃合并成的补偿(1) = 3，绝不是 4。
    assert calls == ["reconcile", "reconcile", "reconcile"]


@pytest.mark.asyncio
async def test_client_reconnects_after_connection_error(tmp_path: Path) -> None:
    """连接失败按退避重连；重连成功后事件照常触发 reconcile。"""

    config = _make_config(tmp_path)
    _seed_identity(config)

    calls: list[str] = []
    attempts: list[str] = []
    stop = asyncio.Event()

    def fake_reconcile(_config: AgentConfig):
        calls.append("reconcile")
        return None  # type: ignore[return-value]

    def fake_event_source(ws_url: str, token: str):
        attempts.append(ws_url)

        class _Stream:
            async def __aenter__(self):
                if len(attempts) == 1:
                    raise ConnectionError("boom")
                return self._gen()

            async def __aexit__(self, *exc):
                return False

            async def _gen(self):
                yield {"type": "desired_state_updated", "generation": 2}
                while len(calls) < 2:
                    await asyncio.sleep(0.01)
                stop.set()

        return _Stream()

    await asyncio.wait_for(
        run_watch(
            config,
            reconcile=fake_reconcile,  # type: ignore[arg-type]
            event_source=fake_event_source,
            initial_backoff=0.01,
            debounce_seconds=0,
            stop_event=stop,
        ),
        timeout=10,
    )

    assert len(attempts) >= 2  # 第一次失败 + 至少一次重连
    assert calls == ["reconcile", "reconcile"]  # startup + 事件


@pytest.mark.asyncio
async def test_hello_triggers_catchup_when_local_generation_behind(tmp_path: Path) -> None:
    """断线重连后 hello 携带的 generation 领先本地时必须立即追赶。

    回归锁：此前断线期间漏掉的变更只能等兜底周期（最长 300 秒）。
    """

    config = _make_config(tmp_path)
    paths = AgentPaths(config.state_dir, "edge1")
    paths.node_dir.mkdir(parents=True, exist_ok=True)
    save_identity(
        LocalAgentIdentity(
            node_id="edge1", agent_id="a", agent_token="tok", applied_generation=3
        ),
        paths.identity_file,
    )

    calls: list[str] = []
    stop = asyncio.Event()

    def fake_reconcile(_config: AgentConfig):
        calls.append("reconcile")
        return None  # type: ignore[return-value]

    def fake_event_source(ws_url: str, token: str):
        class _Stream:
            async def __aenter__(self):
                return self._gen()

            async def __aexit__(self, *exc):
                return False

            async def _gen(self):
                # 控制面已到 gen=7，本地 3 → 追赶。
                yield {"type": "hello", "node_id": "edge1", "generation": 7}
                while len(calls) < 2:
                    await asyncio.sleep(0.01)
                stop.set()

        return _Stream()

    await asyncio.wait_for(
        run_watch(
            config,
            reconcile=fake_reconcile,  # type: ignore[arg-type]
            event_source=fake_event_source,
            debounce_seconds=0,
            stop_event=stop,
        ),
        timeout=10,
    )

    assert calls == ["reconcile", "reconcile"]  # startup + hello 追赶


@pytest.mark.asyncio
async def test_stale_desired_state_event_is_deduplicated(tmp_path: Path) -> None:
    """generation 不高于本地已应用值的门铃必须去重跳过（幂等门铃语义）。"""

    config = _make_config(tmp_path)
    paths = AgentPaths(config.state_dir, "edge1")
    paths.node_dir.mkdir(parents=True, exist_ok=True)
    save_identity(
        LocalAgentIdentity(
            node_id="edge1", agent_id="a", agent_token="tok", applied_generation=5
        ),
        paths.identity_file,
    )

    calls: list[str] = []
    stop = asyncio.Event()

    def fake_reconcile(_config: AgentConfig):
        calls.append("reconcile")
        return None  # type: ignore[return-value]

    def fake_event_source(ws_url: str, token: str):
        class _Stream:
            async def __aenter__(self):
                return self._gen()

            async def __aexit__(self, *exc):
                return False

            async def _gen(self):
                # hello 与本地一致：不追赶。
                yield {"type": "hello", "node_id": "edge1", "generation": 5}
                # 旧代/重复门铃：去重。
                yield {"type": "desired_state_updated", "generation": 4}
                yield {"type": "desired_state_updated", "generation": 5}
                # 真正的新代：触发。
                yield {"type": "desired_state_updated", "generation": 6}
                while len(calls) < 2:
                    await asyncio.sleep(0.01)
                stop.set()

        return _Stream()

    await asyncio.wait_for(
        run_watch(
            config,
            reconcile=fake_reconcile,  # type: ignore[arg-type]
            event_source=fake_event_source,
            debounce_seconds=0,
            stop_event=stop,
        ),
        timeout=10,
    )

    # startup + 仅 gen=6 触发的一次 = 2；gen 4/5 与 hello 都不应触发。
    assert calls == ["reconcile", "reconcile"]


@pytest.mark.asyncio
async def test_run_watch_survives_fallback_timeout_then_event(tmp_path: Path) -> None:
    """回归：fallback 超时后又收到事件，常驻进程不能崩。

    旧实现每次超时都 ``next_event.cancel()`` 再 ``await next_event``，抛出的
    ``CancelledError`` 继承自 ``BaseException``，``contextlib.suppress(Exception)``
    抓不到，导致整个 watch 循环（乃至守护进程）退出。
    """

    config = _make_config(tmp_path)
    _seed_identity(config)

    reasons: list[str] = []
    stop = asyncio.Event()

    def fake_reconcile(_config: AgentConfig):
        reasons.append("reconcile")
        return None  # type: ignore[return-value]

    def fake_event_source(ws_url: str, token: str):
        class _Stream:
            async def __aenter__(self):
                return self._gen()

            async def __aexit__(self, *exc):
                return False

            async def _gen(self):
                # 先静默一段时间，触发至少一次 fallback 超时分支……
                await asyncio.sleep(0.15)
                # ……再推一个真正的门铃事件，验证同一个事件 future 仍可用。
                yield {"type": "desired_state_updated", "generation": 2}
                stop.set()

        return _Stream()

    await asyncio.wait_for(
        run_watch(
            config,
            reconcile=fake_reconcile,  # type: ignore[arg-type]
            event_source=fake_event_source,
            fallback_interval=0.05,
            stop_event=stop,
        ),
        timeout=5,
    )

    # startup(1) + 至少一次 fallback + 事件触发的 reconcile；关键是循环没有崩溃退出。
    assert len(reasons) >= 3

