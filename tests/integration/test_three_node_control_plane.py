from __future__ import annotations

"""三节点控制面端到端集成测试（真实 HTTP + WebSocket）。

这条用例不打桩网络：它真的起一个 uvicorn 控制面，provision 三个内部节点，
再拉起三个 **常驻 watch** agent（用真实 ``websockets`` 连各自的私有通道），
然后验证：

1. 三个 agent 各自注册、拉取、reconcile 成功（applied_generation 落地）；
2. 对单个节点下发 ``desired_state_updated`` 时，**只有该节点的 agent** 收到事件
   并重新 reconcile（generation 前进），另外两个 agent 不受影响——即私有通道隔离；
3. 每个 agent 把**自己节点**的配置渲染到了各自的状态目录。

这就是「多节点调试」的可重复证据：控制面 + 三 agent 真实联动。
"""

import asyncio
import socket
from pathlib import Path

import httpx
import pytest
import uvicorn

from agent.adapters import Adapters
from agent.collectors.docker import DockerObserver, ObservedProject
from agent.core.config import AgentConfig
from agent.core.identity import load_identity
from agent.core.paths import AgentPaths
from agent.orchestrator import run_once
from agent.watch import run_watch
from app.core.config import ControlServerConfig
from app.main import create_app

from dn42_schemas.testing import build_local_three_node_states

pytestmark = pytest.mark.asyncio

_INTERNAL_NODE_IDS = ("edge1", "edge2", "edge3")
_ENROLLMENT_TOKEN = "enroll-token"


class _NullObserver(DockerObserver):
    """不接触真实 Docker 的观察器：reconcile 渲染路径仍可跑完。"""

    def __init__(self) -> None:
        super().__init__(docker_factory=lambda: None)

    def observe_project(self, state) -> ObservedProject:  # noqa: ANN001
        return ObservedProject(project_name="integration-test")


class _NoNetworkExec:
    """模拟容器内 exec 不可用：WG/BGP 维度退化为未采集。"""

    def run(self, container, argv):  # noqa: ANN001
        return 1, "", ""

    def put_file(self, *args, **kwargs) -> None:
        return None


def _reconcile(config: AgentConfig):
    adapters = Adapters.build(
        config,
        docker_observer=_NullObserver(),
        container_exec=_NoNetworkExec(),
    )
    try:
        return run_once(config, adapters)
    finally:
        adapters.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait(predicate, *, timeout: float, interval: float = 0.2) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if await predicate() if asyncio.iscoroutinefunction(predicate) else predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met within timeout")


def _applied_generation(state_dir: Path, node_id: str) -> int | None:
    paths = AgentPaths(state_dir, node_id)
    if not paths.identity_file.exists():
        return None
    return load_identity(paths.identity_file).applied_generation


async def test_three_node_control_plane_end_to_end(tmp_path: Path) -> None:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    config = ControlServerConfig(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'control.db').as_posix()}",
        enrollment_token=_ENROLLMENT_TOKEN,
        admin_token="it-admin-token",
    )
    app = create_app(config)

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    server.install_signal_handlers = lambda: None  # type: ignore[assignment]
    server_task = asyncio.create_task(server.serve())
    try:
        await _wait(lambda: server.started, timeout=20)

        # 1) provision 三个内部节点。
        states = {
            s.node.node_id: s
            for _d, s in build_local_three_node_states()
            if s.node.node_id in _INTERNAL_NODE_IDS
        }
        assert set(states) == set(_INTERNAL_NODE_IDS)

        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=30.0,
            headers={"Authorization": "Bearer it-admin-token"},
        ) as http:
            for node_id, state in states.items():
                resp = await http.post(
                    "/api/v1/admin/provision",
                    json={
                        "state": state.model_dump(mode="json"),
                        "agent_token": f"{node_id}-token",
                    },
                )
                assert resp.status_code == 201, resp.text

            # 2) 拉起三个常驻 watch agent，各自独立状态目录。
            agents: dict[str, dict] = {}
            for node_id in _INTERNAL_NODE_IDS:
                state_dir = tmp_path / f"agent-{node_id}"
                agent_cfg = AgentConfig(
                    controller_url=base_url,
                    enrollment_token=_ENROLLMENT_TOKEN,
                    requested_node_id=node_id,
                    state_dir=state_dir,
                    # 与三节点 compose 演示一致：无 Docker 环境下只渲染不部署。
                    mode="write-rendered",
                )
                stop = asyncio.Event()
                task = asyncio.create_task(
                    run_watch(
                        agent_cfg,
                        reconcile=_reconcile,
                        fallback_interval=999.0,
                        stop_event=stop,
                    )
                )
                agents[node_id] = {"state_dir": state_dir, "stop": stop, "task": task}

            # 等三个 agent 的首轮 reconcile 落地（identity 有 applied_generation）。
            async def _all_reconciled() -> bool:
                return all(
                    _applied_generation(a["state_dir"], nid) is not None
                    for nid, a in agents.items()
                )

            await _wait(_all_reconciled, timeout=40)

            baseline = {
                nid: _applied_generation(a["state_dir"], nid) for nid, a in agents.items()
            }
            for nid, gen in baseline.items():
                assert gen is not None, f"{nid} 未完成首轮 reconcile"

            # 等目标节点的私有通道连上（用 snapshot_request 探测，不递增世代）。
            target = "edge2"

            async def _target_subscribed() -> bool:
                r = await http.post(
                    f"/api/v1/admin/nodes/{target}/notify",
                    json={"event": "snapshot_request", "reason": "probe"},
                )
                return r.status_code == 200 and r.json()["subscribers"] >= 1

            await _wait(_target_subscribed, timeout=20)

            # 3) 只对 target 下发 desired_state_updated（递增世代）。
            resp = await http.post(
                f"/api/v1/admin/nodes/{target}/notify",
                json={"event": "desired_state_updated"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["delivered"] == 1  # 只投递给 target 的私有通道

            # target 的 agent 重新 reconcile，applied_generation 前进。
            async def _target_advanced() -> bool:
                cur = _applied_generation(agents[target]["state_dir"], target)
                base = baseline[target]
                return cur is not None and base is not None and cur > base

            await _wait(_target_advanced, timeout=30)

            # 隔离性：另外两个 agent 的 applied_generation 不变。
            await asyncio.sleep(1.0)
            for nid in _INTERNAL_NODE_IDS:
                if nid == target:
                    continue
                assert _applied_generation(agents[nid]["state_dir"], nid) == baseline[nid], (
                    f"{nid} 不应因 {target} 的事件而 reconcile"
                )

            # 每个 agent 都把自己节点的配置渲染到了各自目录。
            for nid, a in agents.items():
                rendered = AgentPaths(a["state_dir"], nid).rendered_dir
                assert rendered.exists(), f"{nid} 未渲染输出"
                assert any(rendered.rglob("*")), f"{nid} 渲染目录为空"

            # 收尾：停 agent。
            for a in agents.values():
                a["stop"].set()
            await asyncio.gather(*(a["task"] for a in agents.values()), return_exceptions=True)
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=20)
