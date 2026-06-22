from __future__ import annotations

"""节点 agent 主循环 ``run_once`` 的集成测试。

``run_once`` 是一个 “从注册到上报” 的闭环：注册 → 拉 desired-state →
渲染 → apply → 上报快照 + reconciliation 报告 + apply 结果。本文件
使用 ``httpx.MockTransport`` 冷起一个虚拟控制面进行验证：

* ``/api/v1/agent/register`` 返回 ``BootstrapStatus.ACCEPTED`` 与临时 token、
  ``/desired-state`` 返回验证过的 sample state，后续上报接口都接受 200。
* DockerObserver 被替换为 fake，首次返回空集模拟 “clean install”，
  第二次返回资源“都跑起来”的接近”状态。
* 验证上报报文中 ``snapshot``、``report``、``apply_result`` 三个 body
  被控制面接收且字段与预期一致（generation、node_id、apply status 等）。
"""

import json
from pathlib import Path

import httpx

from dn42_schemas import AgentRegistrationResponse, BootstrapStatus
from dn42_schemas.testing import build_hkg1_example_state

from agent.adapters import Adapters
from agent.client.controller import ControllerClient
from agent.collectors.docker import DockerObserver, ObservedProject
from agent.collectors.inventory import build_host_inventory
from agent.core.config import AgentConfig
from agent.core.identity import LocalAgentIdentity, load_identity, save_identity
from agent.core.naming import node_project_name
from agent.core.paths import AgentPaths
from agent.orchestrator import run_once
from agent.apply.executor import DeployResult


class _NoNetworkExec:
    """单测隔离：模拟容器内 exec 不可用，WG/BGP 维度退化为未采集。"""

    def run(self, container: str, argv: list[str]) -> tuple[int, str, str]:
        return 1, "", ""

    def put_file(self, *args, **kwargs) -> None:
        return None


class _RecordingExec:
    """记录 (container, argv) 并按 argv 末尾分发响应的假 ContainerExec。"""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._responses = responses or {}

    def run(self, container: str, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append((container, argv))
        if argv and argv[-1] in self._responses:
            return 0, self._responses[argv[-1]], ""
        return 1, "", ""

    def put_file(self, *args, **kwargs) -> None:
        return None


def _adapters(config, **overrides):
    """测试装配：单一入口替代散落的注入参数。"""

    overrides.setdefault("container_exec", _NoNetworkExec())
    overrides.setdefault(
        "inventory_builder", lambda hostname=None: build_host_inventory(hostname=hostname)
    )
    return Adapters.build(config, **overrides)


class _EmptyObserver(DockerObserver):
    """部署前永远报告 0 个容器；部署后显示全部已就绪。

    第二次观察的 config_hash 与 planner 同源（来自容器定义），需要知道
    state_dir 才能推导 planner 使用的 rendered_dir。
    """

    def __init__(self, state, state_dir: Path) -> None:
        super().__init__(docker_factory=lambda: None)
        self._state = state
        self._state_dir = state_dir
        self._call_count = 0

    def observe_project(self, state):  # type: ignore[override]
        from dn42_schemas import ObservedContainer, RuntimeResourceStatus

        from agent.planner.definition import build_node_definitions

        self._call_count += 1
        if self._call_count == 1:
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


def _build_mock_controller(state, captured: dict[str, dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        if path == "/api/v1/agent/register":
            payload = AgentRegistrationResponse(
                status=BootstrapStatus.ACCEPTED,
                node_id=state.node.node_id,
                agent_id=f"{state.node.node_id}-agent",
                agent_token="fake-token",
                desired_state_generation=state.generation,
            )
            return httpx.Response(200, json=payload.model_dump(mode="json"))
        if path == "/api/v1/agent/desired-state":
            return httpx.Response(200, json=state.model_dump(mode="json"))
        if path == "/api/v1/agent/recovery-public-key":
            return httpx.Response(200, json={"configured": False})
        if path == "/api/v1/agent/wireguard-keys":
            captured["wireguard_keys"] = body
            return httpx.Response(
                200, json={"node_id": body["node_id"], "accepted": True, "status": "stored"}
            )
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

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://controller.test")


def test_orchestrator_writes_rendered_via_controller(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    captured: dict[str, dict] = {}
    config = AgentConfig(
        controller_url="http://controller.test",
        enrollment_token="fake-enrollment-token",
        requested_node_id=state.node.node_id,
        state_dir=tmp_path,
        mode="write-rendered",
    )

    client = ControllerClient(_build_mock_controller(state, captured))
    result = run_once(
        config, _adapters(config, controller=client, docker_observer=_EmptyObserver(state, tmp_path))
    )

    assert result.source == "controller"
    assert result.mode == "write-rendered"
    assert result.state.node.node_id == state.node.node_id

    paths = AgentPaths(state_dir=tmp_path, node_id=state.node.node_id)
    assert (paths.rendered_dir / "bird" / "bird.conf").exists()
    # 容器编排不渲染文件：渲染产物里绝不应再出现 docker-compose.yml。
    assert not (paths.rendered_dir / "docker-compose.yml").exists()
    assert paths.identity_file.exists()
    assert paths.desired_state_file.exists()

    assert captured["snapshot"]["node_id"] == state.node.node_id
    assert captured["report"]["status"] == "succeeded"
    assert captured["apply_result"]["status"] == "succeeded"
    applied_files = captured["apply_result"]["applied_files"]
    assert applied_files  # 首次渲染目录不存在 → 全部 create
    assert all(item["action"] == "create" for item in applied_files)
    assert any(item["path"] == "bird/bird.conf" for item in applied_files)
    assert result.controller_acks["registration"]["agent_token"] == "fake-token"


def test_orchestrator_apply_mode_deploys_via_executor(tmp_path: Path) -> None:
    """apply 模式把渲染目录与容器计划原样交给注入的部署边界。"""

    state = build_hkg1_example_state()
    captured: dict[str, dict] = {}
    config = AgentConfig(
        controller_url="http://controller.test",
        enrollment_token="t",
        requested_node_id=state.node.node_id,
        state_dir=tmp_path,
        local_convergence=False,
    )

    deploy_calls: list[tuple[str, int]] = []

    class _FakeApplyExecutor:
        def deploy(self, *, state, container_plan):
            deploy_calls.append((state.node.node_id, len(container_plan.steps)))
            return DeployResult(
                backend="docker-api",
                succeeded=True,
                raw={"backend": "docker-api", "succeeded": True},
            )

    client = ControllerClient(_build_mock_controller(state, captured))

    result = run_once(
        config,
        _adapters(
            config,
            controller=client,
            apply_executor=_FakeApplyExecutor(),
            docker_observer=_EmptyObserver(state, tmp_path),
        ),
    )

    assert result.mode == "apply"
    assert result.deploy_result is not None
    assert result.deploy_result.backend == "docker-api"
    assert result.deploy_result.succeeded
    enabled = len([s for s in state.runtime.services if s.enabled])
    assert deploy_calls == [(state.node.node_id, enabled)]
    assert captured["report"]["status"] == "succeeded"


def test_orchestrator_aborts_on_wireguard_key_conflict(tmp_path: Path) -> None:
    """公钥与控制面记录冲突（409）时，apply 必须中止——不得用偏离密钥拉隧道。

    回归锁：``_sync_wireguard_keys`` 在 409 时把 ControllerError 上抛中止 reconcile。
    """

    import pytest

    from agent.core.errors import ControllerError

    state = build_hkg1_example_state()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
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
            return httpx.Response(200, json=state.model_dump(mode="json"))
        if path == "/api/v1/agent/recovery-public-key":
            return httpx.Response(200, json={"configured": False})
        if path == "/api/v1/agent/wireguard-keys":
            return httpx.Response(409, json={"detail": "public key conflict"})
        raise AssertionError(f"unexpected path: {path}")

    client = ControllerClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://controller.test")
    )
    config = AgentConfig(
        controller_url="http://controller.test",
        enrollment_token="t",
        requested_node_id=state.node.node_id,
        state_dir=tmp_path,
        mode="apply",
        local_convergence=False,
    )

    with pytest.raises(ControllerError) as excinfo:
        run_once(config, _adapters(config, controller=client, docker_observer=_EmptyObserver(state, tmp_path)))
    assert excinfo.value.status_code == 409


def test_orchestrator_convergence_failure_fails_apply(tmp_path: Path) -> None:
    """收敛失败（隧道/BGP 没热加载成功）必须让 apply_status=FAILED，不推进世代。

    假绿 Fix 1 回归锁：部署成功但 convergence.ok=False 时，旧逻辑把整次 apply
    标 SUCCEEDED（"apply 成功但隧道没起来"）。
    """

    state = build_hkg1_example_state()
    captured: dict[str, dict] = {}

    class _FakeApplyExecutor:
        def deploy(self, *, state, container_plan):
            return DeployResult(backend="docker-api", succeeded=True, raw={"succeeded": True})

    client = ControllerClient(_build_mock_controller(state, captured))
    config = AgentConfig(
        controller_url="http://controller.test",
        enrollment_token="t",
        requested_node_id=state.node.node_id,
        state_dir=tmp_path,
        mode="apply",
        local_convergence=True,  # 收敛开启，_NoNetworkExec 让每个 apply 脚本非零退出
    )

    result = run_once(
        config,
        _adapters(
            config,
            controller=client,
            apply_executor=_FakeApplyExecutor(),
            docker_observer=_EmptyObserver(state, tmp_path),
        ),
    )

    assert result.apply_status.value == "failed"
    assert result.convergence is not None and not result.convergence.ok
    assert captured["apply_result"]["status"] == "failed"
    # 世代不推进：下一轮会重试收敛（容器已就绪，只重放收敛，不抖）。
    paths = AgentPaths(state_dir=tmp_path, node_id=state.node.node_id)
    assert load_identity(paths.identity_file).applied_generation != state.generation


def test_orchestrator_publish_failure_does_not_advance_generation(tmp_path: Path) -> None:
    """上报失败时不得推进 applied_generation。

    假绿 Fix 3 回归锁：apply 本地成功但上报抛错时，旧逻辑已把 applied_generation
    落盘成新世代——控制面没收到却以为已应用，且 WS 去重/追赶基于该值，使这一代
    再也不触发重报，视图长期脱节。
    """

    import pytest

    from agent.core.errors import ControllerError

    state = build_hkg1_example_state()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
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
            return httpx.Response(200, json=state.model_dump(mode="json"))
        if path == "/api/v1/agent/recovery-public-key":
            return httpx.Response(200, json={"configured": False})
        if path == "/api/v1/agent/wireguard-keys":
            body = json.loads(request.content.decode("utf-8")) if request.content else {}
            return httpx.Response(
                200, json={"node_id": body["node_id"], "accepted": True, "status": "stored"}
            )
        if path == "/api/v1/agent/runtime-snapshot":
            return httpx.Response(200, json={"accepted": True})
        if path == "/api/v1/agent/reconciliation-report":
            # 上报中途失败（非 401，不可自愈）。
            return httpx.Response(500, json={"detail": "boom"})
        raise AssertionError(f"unexpected path: {path}")

    client = ControllerClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://controller.test")
    )
    config = AgentConfig(
        controller_url="http://controller.test",
        enrollment_token="t",
        requested_node_id=state.node.node_id,
        state_dir=tmp_path,
        mode="apply",
        local_convergence=False,  # 隔离：apply 本地成功，只让上报失败
    )

    class _OkExecutor:
        def deploy(self, *, state, container_plan):
            return DeployResult(backend="docker-api", succeeded=True, raw={"succeeded": True})

    with pytest.raises(ControllerError):
        run_once(
            config,
            _adapters(
                config,
                controller=client,
                apply_executor=_OkExecutor(),
                docker_observer=_EmptyObserver(state, tmp_path),
            ),
        )

    # 上报失败 → applied_generation 未推进到本代。
    paths = AgentPaths(state_dir=tmp_path, node_id=state.node.node_id)
    assert load_identity(paths.identity_file).applied_generation != state.generation


def test_orchestrator_plan_only_skips_writing_and_deployment(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    config = AgentConfig(state_dir=tmp_path, mode="plan-only")

    result = run_once(config, _adapters(config, docker_observer=_EmptyObserver(state, tmp_path)))

    assert result.mode == "plan-only"
    assert result.deploy_result is None
    assert result.apply_status.value == "skipped"
    paths = AgentPaths(state_dir=tmp_path, node_id=state.node.node_id)
    assert not (paths.rendered_dir / "bird" / "bird.conf").exists()
    assert paths.desired_state_file.exists()  # 缓存仍然写入


def test_orchestrator_collects_wireguard_and_bgp_via_network_runner(tmp_path: Path) -> None:
    """生产路径的 WG/BGP 采集必须真正接线：snapshot 带回观测，drift 检查生效。

    回归锁（review finding #5）：此前 orchestrator 从不注入网络观察器，
    WG/BGP 漂移检测在生产路径上是死代码。
    """

    from dn42_schemas import InterfaceKind
    from dn42_templates import bird_protocol_name

    state = build_hkg1_example_state()
    wg_ifaces = [i for i in state.interfaces if i.kind == InterfaceKind.WIREGUARD]
    assert wg_ifaces, "示例 state 应包含 WireGuard 接口"
    sessions = [s for s in state.bgp_sessions if s.enabled]
    assert sessions, "示例 state 应包含 enabled BGP 会话"

    # 构造与期望一致的 `wg show all dump` 输出（端口匹配、各 1 个 peer）。
    dump_lines = []
    for iface in wg_ifaces:
        port = iface.listen_port or 0
        dump_lines.append(f"{iface.name}\tprivkey\tpubkey\t{port}\toff")
        dump_lines.append(f"{iface.name}\tpeer-pub\t(none)\t192.0.2.1:51820\t0.0.0.0/0\t0\t0\t0\toff")
    wg_dump = "\n".join(dump_lines) + "\n"

    # 第一个会话故意停在 Active（未建立），其余 Established。
    bird_lines = []
    for index, session in enumerate(sessions):
        proto = bird_protocol_name(session.name)
        info = "Active" if index == 0 else "Established"
        bird_lines.append(f"{proto}    BGP        ---        up         2026-06-10    {info}")
    birdc_output = "\n".join(bird_lines) + "\n"

    network_exec = _RecordingExec(responses={"dump": wg_dump, "protocols": birdc_output})

    config = AgentConfig(state_dir=tmp_path, mode="write-rendered")
    result = run_once(
        config,
        _adapters(config, docker_observer=_EmptyObserver(state, tmp_path), container_exec=network_exec),
    )

    # 两路采集都通过容器内 exec 发生在对应容器里。
    assert any(argv[-1] == "dump" for _container, argv in network_exec.calls)
    assert any(argv[-1] == "protocols" for _container, argv in network_exec.calls)

    # snapshot 带回了真实观测。
    assert {w.name for w in result.snapshot.wireguard_interfaces} == {i.name for i in wg_ifaces}
    assert all(w.peer_count == 1 for w in result.snapshot.wireguard_interfaces)
    assert len(result.snapshot.bgp_protocols) == len(sessions)

    # 未建立的 BGP 会话产生 drift —— 漂移检测不再是死代码。
    bgp_drift = [d for d in result.report.drift if d.component == "bgp"]
    assert [d.name for d in bgp_drift] == [sessions[0].name]
    # 端口与 peer 都匹配：不应有 WireGuard 假阳性。
    assert not [d for d in result.report.drift if d.component == "wireguard"]


def test_orchestrator_skips_network_observation_without_containers(tmp_path: Path) -> None:
    """没有任何受管容器时不应尝试容器内 exec（全新节点 / 无 Docker 环境）。"""

    network_exec = _RecordingExec()

    config = AgentConfig(state_dir=tmp_path, mode="plan-only")
    result = run_once(
        config,
        _adapters(
            config,
            docker_observer=DockerObserver(docker_factory=lambda: None),
            container_exec=network_exec,
        ),
    )

    assert network_exec.calls == []
    assert result.snapshot.wireguard_interfaces == []
    assert result.snapshot.bgp_protocols == []


def test_orchestrator_offline_writes_rendered_to_state_dir(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    config = AgentConfig(state_dir=tmp_path, mode="write-rendered")

    result = run_once(config, _adapters(config, docker_observer=_EmptyObserver(state, tmp_path)))

    assert result.source == "built-in-example"
    assert result.mode == "write-rendered"
    paths = AgentPaths(state_dir=tmp_path, node_id=state.node.node_id)
    assert (paths.rendered_dir / "bird" / "bird.conf").exists()


def test_orchestrator_recovers_from_revoked_token(tmp_path: Path) -> None:
    """token 被轮换/撤销后 agent 自愈（review 缺陷 B 回归锁）。

    旧实现只在本地无 token 时注册，401 会让常驻进程永远失败循环、
    需要人工删 identity。现在：401 → 作废本地 token → 凭 enrollment
    重新注册 → 本轮 reconcile 正常完成，新 token 落盘。
    """

    state = build_hkg1_example_state()
    node_id = state.node.node_id
    captured: dict[str, dict] = {}
    register_calls = {"count": 0}

    # 预置"已注册但 token 已被控制面撤销"的身份。
    paths = AgentPaths(tmp_path, node_id)
    paths.ensure()
    save_identity(
        LocalAgentIdentity(node_id=node_id, agent_id=f"{node_id}-agent", agent_token="stale-token"),
        paths.identity_file,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        auth = request.headers.get("authorization")
        if path == "/api/v1/agent/register":
            register_calls["count"] += 1
            payload = AgentRegistrationResponse(
                status=BootstrapStatus.ACCEPTED,
                node_id=node_id,
                agent_id=f"{node_id}-agent",
                agent_token="fresh-token",
                desired_state_generation=state.generation,
            )
            return httpx.Response(200, json=payload.model_dump(mode="json"))
        if auth == "Bearer stale-token":
            return httpx.Response(401, json={"detail": "token revoked"})
        if path == "/api/v1/agent/desired-state":
            return httpx.Response(200, json=state.model_dump(mode="json"))
        if path == "/api/v1/agent/recovery-public-key":
            return httpx.Response(200, json={"configured": False})
        if path == "/api/v1/agent/wireguard-keys":
            return httpx.Response(
                200, json={"node_id": node_id, "accepted": True, "status": "stored"}
            )
        body = json.loads(request.content.decode("utf-8")) if request.content else {}
        captured[path.rsplit("/", 1)[-1]] = body
        return httpx.Response(200, json={"accepted": True})

    client = ControllerClient(
        httpx.Client(transport=httpx.MockTransport(handler), base_url="http://controller.test")
    )
    config = AgentConfig(
        controller_url="http://controller.test",
        enrollment_token="enroll",
        requested_node_id=node_id,
        state_dir=tmp_path,
        mode="write-rendered",
    )

    result = run_once(
        config, _adapters(config, controller=client, docker_observer=_EmptyObserver(state, tmp_path))
    )

    assert register_calls["count"] == 1  # 401 后恰好重注册一次
    assert result.apply_status.value == "succeeded"
    assert captured["apply-result"]["status"] == "succeeded"
    # 新 token 已持久化，下一轮直接可用。
    assert load_identity(paths.identity_file).agent_token == "fresh-token"
