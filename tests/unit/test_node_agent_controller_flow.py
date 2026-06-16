from __future__ import annotations

"""验证 node-agent 与 control-server 之间的端到端注册 + reconcile 流程。

模块级 agent 单测在 apps/node-agent/agent/tests/ 下，本文件聚焦
跨 app 的协议契约：通过 `httpx.MockTransport` 模拟 Control Server，
驱动 `agent.run_once`，并断言关键 endpoint 和 payload 都按 docs/api.md 走。
"""

import json
from pathlib import Path

import httpx

from dn42_schemas import (
    AgentRegistrationResponse,
    BootstrapStatus,
    ObservedContainer,
    RuntimeResourceStatus,
)
from dn42_schemas.testing import build_hkg1_example_state

from agent.adapters import Adapters
from agent.client.controller import ControllerClient
from agent.collectors.docker import DockerObserver, ObservedProject

from agent.core.config import AgentConfig
from agent.core.naming import node_project_name
from agent.core.paths import AgentPaths
from agent.orchestrator import run_once
from agent.planner.definition import build_node_definitions


class _NoNetworkExec:
    """单测隔离：模拟容器内 exec 不可用，WG/BGP 维度退化为未采集。"""

    def run(self, container: str, argv: list[str]) -> tuple[int, str, str]:
        return 1, "", ""

    def put_file(self, *args, **kwargs) -> None:
        return None


def _adapters(config: AgentConfig, controller: ControllerClient, observer) -> Adapters:
    return Adapters.build(
        config, controller=controller, docker_observer=observer, container_exec=_NoNetworkExec()
    )


class _StubObserver(DockerObserver):
    def __init__(self, state, with_running: bool, state_dir: Path) -> None:
        super().__init__(docker_factory=lambda: None)
        self._state = state
        self._with_running = with_running
        self._state_dir = state_dir

    def observe_project(self, state):  # type: ignore[override]
        if not self._with_running:
            return ObservedProject(project_name=node_project_name(state))
        project = node_project_name(state)
        rendered_dir = AgentPaths(
            state_dir=self._state_dir, node_id=state.node.node_id
        ).rendered_dir
        containers = [
            ObservedContainer(
                name=definition.container_name,
                role=None,
                config_hash=definition.config_hash,
                status=RuntimeResourceStatus.RUNNING,
                healthy=True,
            )
            for definition in build_node_definitions(state, rendered_dir).values()
        ]
        return ObservedProject(project_name=project, containers=containers)


def _mock_controller(state, captured: dict[str, dict]) -> ControllerClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        if path == "/api/v1/agent/register":
            return httpx.Response(
                200,
                json=AgentRegistrationResponse(
                    status=BootstrapStatus.ACCEPTED,
                    node_id=state.node.node_id,
                    agent_id=f"{state.node.node_id}-agent",
                    agent_token="fake-token",
                    desired_state_generation=state.generation,
                ).model_dump(mode="json"),
            )
        if path == "/api/v1/agent/desired-state":
            assert request.headers.get("authorization") == "Bearer fake-token"
            return httpx.Response(200, json=state.model_dump(mode="json"))
        if path == "/api/v1/agent/runtime-snapshot":
            captured["snapshot"] = body
            return httpx.Response(200, json={"accepted": True})
        if path == "/api/v1/agent/reconciliation-report":
            captured["report"] = body
            return httpx.Response(200, json={"accepted": True})
        if path == "/api/v1/agent/apply-result":
            captured["apply_result"] = body
            return httpx.Response(200, json={"accepted": True})
        raise AssertionError(f"unexpected path: {path}")

    return ControllerClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://controller.test")
    )


def test_agent_bootstrap_flow_against_fake_controller(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    captured: dict[str, dict] = {}
    config = AgentConfig(
        controller_url="http://controller.test",
        enrollment_token="enroll",
        requested_node_id=state.node.node_id,
        state_dir=tmp_path,
        mode="write-rendered",
    )

    result = run_once(
        config,
        _adapters(config, _mock_controller(state, captured), _StubObserver(state, with_running=True, state_dir=tmp_path)),
    )

    assert result.source == "controller"
    assert result.mode == "write-rendered"
    paths = AgentPaths(state_dir=tmp_path, node_id=state.node.node_id)
    assert (paths.rendered_dir / "bird" / "bird.conf").exists()
    assert captured["snapshot"]["node_id"] == state.node.node_id
    assert captured["report"]["status"] == "succeeded"
    assert captured["apply_result"]["generation"] == state.generation


def test_agent_reuses_persisted_token_on_second_run(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    captured: dict[str, dict] = {}
    config = AgentConfig(
        controller_url="http://controller.test",
        enrollment_token="enroll",
        requested_node_id=state.node.node_id,
        state_dir=tmp_path,
        mode="write-rendered",
    )

    register_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/agent/register":
            register_calls["count"] += 1
            return httpx.Response(
                200,
                json=AgentRegistrationResponse(
                    status=BootstrapStatus.ACCEPTED,
                    node_id=state.node.node_id,
                    agent_id=f"{state.node.node_id}-agent",
                    agent_token="fake-token",
                    desired_state_generation=state.generation,
                ).model_dump(mode="json"),
            )
        if path == "/api/v1/agent/desired-state":
            return httpx.Response(200, json=state.model_dump(mode="json"))
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        captured[path] = body
        return httpx.Response(200, json={"accepted": True})

    def make_client() -> ControllerClient:
        return ControllerClient(
            httpx.Client(transport=httpx.MockTransport(handler), base_url="http://controller.test")
        )

    run_once(
        config,
        _adapters(config, make_client(), _StubObserver(state, with_running=True, state_dir=tmp_path)),
    )
    run_once(
        config,
        _adapters(config, make_client(), _StubObserver(state, with_running=True, state_dir=tmp_path)),
    )

    assert register_calls["count"] == 1
