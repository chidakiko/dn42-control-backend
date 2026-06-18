from __future__ import annotations

"""节点 agent Docker API apply 后端的集成测试。

执行层的核心不变量：**严格照单执行，绝不自行决策**。

* ``topologically_sorted_services``：router-netns 或 rpki-cache 必须位于
  启动顺序顶部，wg-gateway 必须早于 bird-router，以保证依赖顺序。
* docker-api 后端：
  - deploy 完全由传入的 `ContainerPlan` 驱动——KEEP 一概不碰（即使 Docker
    实际状态与计划不符也不二次决策）、REMOVE 孤儿被真实删除、
    (re)create 按拓扑序、照 step 携带的 `ContainerDefinition` 落地；
  - 镜像只为计划要 (re)create 的服务准备（定义内 Dockerfile 内存构建）；
  - payload -> SDK 参数转换（binds / ports / healthcheck）是纯函数。
* writer：严格按 `FilePlan` 写入/删除，返回真实执行结果。
* executor：唯一部署路径是 docker-api，照单转发计划。
"""

import os
import stat
from pathlib import Path

import pytest

from dn42_runtime import build_file_plan
from dn42_schemas import ObservedContainer, RuntimeResourceStatus
from dn42_schemas.testing import build_hkg1_example_state

from agent.apply.docker_api import (
    DockerApiBackend,
    exposed_ports_from_payload,
    healthcheck_config_from_payload,
    binds_mapping,
    port_bindings_from_payload,
    topologically_sorted_services,
)
from agent.apply.executor import ApplyExecutor
from agent.apply.writer import write_rendered_bundle
from agent.planner import build_container_plan, build_node_definitions
from agent.planner.container_plan import ContainerAction
from agent.render.pipeline import render_state


def _write_bundle(bundle, rendered_dir: Path):
    """测试辅助：按当前目录现状构建文件计划并执行写盘。"""

    plan = build_file_plan(
        bundle.files, rendered_dir if rendered_dir.exists() else None, prune=True
    )
    return write_rendered_bundle(bundle, rendered_dir, file_plan=plan)


def _plan_all_create(state, rendered_dir: Path):
    """空观测 → 全部 CREATE 的容器计划。"""

    return build_container_plan(state, [], rendered_dir=rendered_dir)


def _matching_observed(state, rendered_dir: Path) -> list[ObservedContainer]:
    """观测与期望完全一致的容器集合（哈希与 planner 同源）。"""

    return [
        ObservedContainer(
            name=definition.container_name,
            role=None,
            config_hash=definition.config_hash,
            status=RuntimeResourceStatus.RUNNING,
            healthy=True,
        )
        for definition in build_node_definitions(state, rendered_dir).values()
    ]


def _plan_all_keep(state, rendered_dir: Path):
    """观测与期望完全一致 → 全部 KEEP 的容器计划。"""

    return build_container_plan(
        state, _matching_observed(state, rendered_dir), rendered_dir=rendered_dir
    )


def test_topological_sort_starts_with_router_netns_or_rpki() -> None:
    state = build_hkg1_example_state()
    ordered = topologically_sorted_services(state)
    names = [service.name for service in ordered]

    assert names[0] in {"dn42-router-netns", "dn42-rpki-cache"}
    assert names.index("dn42-router-netns") < names.index("dn42-bird-router")
    assert names.index("dn42-wg-gateway") < names.index("dn42-bird-router")


def test_definition_binds_resolve_relative_sources(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    (tmp_path / "bird").mkdir()
    (tmp_path / "scripts").mkdir()

    definitions = build_node_definitions(state, tmp_path)
    bird_payload = definitions["dn42-bird-router"].payload

    binds = binds_mapping(bird_payload)
    bird_key = str((tmp_path / "bird").resolve())
    assert binds[bird_key] == {"bind": "/etc/bird", "mode": "ro"}


def test_port_bindings_uses_protocol_suffix(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    state_dict = state.model_dump(mode="json")
    state_dict["runtime"]["services"][0]["ports"] = [
        {"container_port": 5000, "host_port": 5000, "protocol": "tcp", "host_ip": "127.0.0.1"}
    ]
    rebuilt = state.__class__.model_validate(state_dict)
    service_name = rebuilt.runtime.services[0].name

    payload = build_node_definitions(rebuilt, tmp_path)[service_name].payload
    bindings = port_bindings_from_payload(payload)

    assert bindings == {"5000/tcp": ("127.0.0.1", 5000)}


def test_port_bindings_expands_port_ranges(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    state_dict = state.model_dump(mode="json")
    state_dict["runtime"]["services"][0]["ports"] = [
        {
            "container_port": 51810,
            "container_port_end": 51812,
            "host_port": 52810,
            "host_port_end": 52812,
            "protocol": "udp",
        }
    ]
    rebuilt = state.__class__.model_validate(state_dict)
    service_name = rebuilt.runtime.services[0].name

    payload = build_node_definitions(rebuilt, tmp_path)[service_name].payload
    bindings = port_bindings_from_payload(payload)

    assert bindings == {
        "51810/udp": 52810,
        "51811/udp": 52811,
        "51812/udp": 52812,
    }
    assert exposed_ports_from_payload(payload) == [
        (51810, "udp"),
        (51811, "udp"),
        (51812, "udp"),
    ]


def test_healthcheck_config_converts_seconds_to_nanoseconds(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    netns = next(s for s in state.runtime.services if s.name == "dn42-router-netns")
    assert netns.healthcheck is not None

    payload = build_node_definitions(state, tmp_path)["dn42-router-netns"].payload
    config = healthcheck_config_from_payload(payload)

    assert config is not None
    assert config["Test"][0] == "CMD-SHELL"
    assert config["Interval"] == netns.healthcheck.interval_seconds * 1_000_000_000
    assert config["Timeout"] == netns.healthcheck.timeout_seconds * 1_000_000_000


def test_docker_api_endpoint_config_includes_service_alias(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    bundle = render_state(state)
    _write_bundle(bundle, tmp_path)
    endpoint_configs: list[dict[str, object]] = []

    class FakeAPI:
        def create_host_config(self, **kwargs: object) -> dict[str, object]:
            return kwargs

        def create_endpoint_config(self, **kwargs: object) -> dict[str, object]:
            endpoint_configs.append(kwargs)
            return kwargs

        def create_networking_config(self, config: dict[str, object]) -> dict[str, object]:
            return config

        def create_container(self, **kwargs: object) -> dict[str, str]:
            return {"Id": str(kwargs["name"])}

        def start(self, **kwargs: object) -> None:
            return None

    class FakeImages:
        def build(self, **kwargs: object) -> tuple[object, list[object]]:
            return object(), []

        def get(self, image: str) -> object:
            return object()

    class FakeContainers:
        def get(self, name: str) -> object:
            raise RuntimeError("missing")

    class FakeNetwork:
        attrs = {"IPAM": {"Config": [{"Subnet": "10.254.42.0/24", "Gateway": "10.254.42.1"}]}}

        def reload(self) -> None:
            return None

    class FakeNetworks:
        def list(self, names: list[str]) -> list[FakeNetwork]:
            return [FakeNetwork()]

    class FakeClient:
        api = FakeAPI()
        images = FakeImages()
        containers = FakeContainers()
        networks = FakeNetworks()

        def close(self) -> None:
            return None

    result = DockerApiBackend(docker_factory=lambda: FakeClient()).deploy(
        state, _plan_all_create(state, tmp_path)
    )

    assert result.succeeded
    aliases = endpoint_configs[0]["aliases"]
    assert isinstance(aliases, list)
    assert {
        "dn42-router-netns",
        "dn42-edge1-dn42-router-netns-1",
    } <= set(aliases)


def test_docker_api_strictly_follows_container_plan(tmp_path: Path) -> None:
    """执行端不二次决策（review 缺陷 A 回归锁）。

    计划说全部 KEEP 时，即使 Docker 实际查不到任何容器，后端也**绝不**
    自行判断"该重建了"——决策只属于 planner，执行端照单执行。
    """

    state = build_hkg1_example_state()
    touched: list[str] = []

    class FakeImages:
        def build(self, **kwargs: object) -> tuple[object, list[object]]:
            touched.append("build")
            return object(), []

        def get(self, image: str) -> object:
            touched.append(f"image:{image}")
            return object()

        def pull(self, image: str) -> None:
            touched.append(f"pull:{image}")

    class FakeContainers:
        def get(self, name: str) -> object:
            touched.append(f"containers.get:{name}")
            raise RuntimeError("missing")  # docker 实际是空的

    class FakeNetwork:
        attrs = {"IPAM": {"Config": [{"Subnet": "10.254.42.0/24", "Gateway": "10.254.42.1"}]}}

        def reload(self) -> None:
            return None

    class FakeNetworks:
        def list(self, names: list[str]) -> list[FakeNetwork]:
            return [FakeNetwork()]

    class FakeClient:
        api = None  # 全 KEEP 计划下不会触达 create API
        images = FakeImages()
        containers = FakeContainers()
        networks = FakeNetworks()

        def close(self) -> None:
            return None

    result = DockerApiBackend(docker_factory=lambda: FakeClient()).deploy(
        state, _plan_all_keep(state, tmp_path)
    )

    assert result.succeeded
    assert result.started_containers == []
    assert result.removed_containers == []
    assert result.built_images == []  # KEEP 服务不准备镜像
    assert not any(item.startswith("build") for item in touched)


def test_docker_api_removes_orphan_containers(tmp_path: Path) -> None:
    """计划中的 REMOVE（孤儿）必须被真实删除，且不触碰任何 KEEP 容器。"""

    state = build_hkg1_example_state()
    orphan_name = "dn42-edge1-dn42-old-service-1"
    observed = _matching_observed(state, tmp_path) + [
        ObservedContainer(
            name=orphan_name,
            role=None,
            config_hash="cafe",
            status=RuntimeResourceStatus.STOPPED,
            healthy=None,
        )
    ]
    plan = build_container_plan(state, observed, rendered_dir=tmp_path)
    removed_calls: list[str] = []

    class FakeContainer:
        def __init__(self, name: str) -> None:
            self._name = name

        def remove(self, force: bool = False) -> None:
            assert force
            removed_calls.append(self._name)

    class FakeContainers:
        def get(self, name: str) -> FakeContainer:
            if name == orphan_name:
                return FakeContainer(name)
            raise RuntimeError("missing")

    class FakeNetwork:
        attrs = {"IPAM": {"Config": [{"Subnet": "10.254.42.0/24", "Gateway": "10.254.42.1"}]}}

        def reload(self) -> None:
            return None

    class FakeNetworks:
        def list(self, names: list[str]) -> list[FakeNetwork]:
            return [FakeNetwork()]

    class FakeClient:
        api = None  # 没有 (re)create，不会触达 create API
        images = None
        containers = FakeContainers()
        networks = FakeNetworks()

        def close(self) -> None:
            return None

    result = DockerApiBackend(docker_factory=lambda: FakeClient()).deploy(state, plan)

    assert result.succeeded
    assert removed_calls == [orphan_name]
    assert result.removed_containers == [orphan_name]
    assert result.started_containers == []


def test_underlay_ipam_participates_in_config_hash(tmp_path: Path) -> None:
    """underlay subnet/gateway 必须进容器内容寻址哈希。

    否则 IPAM 变化时容器全判 KEEP，旧容器钉死旧网络，新网络永远落不下去。
    """

    state = build_hkg1_example_state()
    state_dict = state.model_dump(mode="json")
    state_dict["runtime"]["underlay"]["gateway"] = "10.254.42.254"
    rebuilt = state.__class__.model_validate(state_dict)

    old = build_node_definitions(state, tmp_path)
    new = build_node_definitions(rebuilt, tmp_path)

    for name, definition in old.items():
        if "attach" in definition.payload["network"]:
            assert new[name].config_hash != definition.config_hash, name


def test_docker_api_recreates_network_only_after_containers_released(tmp_path: Path) -> None:
    """IPAM 变化的完整链路：先删容器释放 endpoint，再重建网络，最后建容器。

    曾经 `ensure_underlay_network` 在删除容器之前就 `network.remove()`，
    有 endpoint 时必然 active endpoints 失败且不可自愈。
    """

    state = build_hkg1_example_state()
    state_dict = state.model_dump(mode="json")
    state_dict["runtime"]["underlay"]["gateway"] = "10.254.42.254"
    rebuilt = state.__class__.model_validate(state_dict)

    # 观测 = 旧 state 的哈希 → 新 state 下全部 RECREATE。
    observed = _matching_observed(state, tmp_path)
    plan = build_container_plan(rebuilt, observed, rendered_dir=tmp_path)
    assert all(step.action == ContainerAction.RECREATE for step in plan.steps)

    calls: list[str] = []
    attached_names = {definition.container_name for definition in build_node_definitions(state, tmp_path).values()}

    class FakeAPI:
        def create_host_config(self, **kwargs: object) -> dict[str, object]:
            return kwargs

        def create_endpoint_config(self, **kwargs: object) -> dict[str, object]:
            return kwargs

        def create_networking_config(self, config: dict[str, object]) -> dict[str, object]:
            return config

        def create_container(self, **kwargs: object) -> dict[str, str]:
            calls.append(f"container.create:{kwargs['name']}")
            return {"Id": str(kwargs["name"])}

        def start(self, **kwargs: object) -> None:
            return None

    class FakeImages:
        def build(self, **kwargs: object) -> tuple[object, list[object]]:
            return object(), []

        def get(self, image: str) -> object:
            return object()

    class FakeContainer:
        def __init__(self, name: str) -> None:
            self._name = name

        def remove(self, force: bool = False) -> None:
            calls.append(f"container.remove:{self._name}")

    class FakeContainers:
        def get(self, name: str) -> FakeContainer:
            return FakeContainer(name)

    class FakeNetwork:
        # 旧 IPAM + 全部旧容器仍挂着 endpoint。
        attrs = {
            "IPAM": {"Config": [{"Subnet": "10.254.42.0/24", "Gateway": "10.254.42.1"}]},
            "Containers": {
                str(i): {"Name": name} for i, name in enumerate(sorted(attached_names))
            },
        }

        def __init__(self) -> None:
            self.removed = False

        def reload(self) -> None:
            # 容器删光后 endpoint 释放。
            if any(call.startswith("container.remove:") for call in calls):
                self.attrs = {**self.attrs, "Containers": {}}

        def remove(self) -> None:
            self.removed = True
            calls.append("network.remove")

    class FakeNetworks:
        def __init__(self) -> None:
            self.network = FakeNetwork()

        def list(self, names: list[str]) -> list[FakeNetwork]:
            return [] if self.network.removed else [self.network]

        def create(self, name: str, **kwargs: object) -> object:
            calls.append("network.create")
            return object()

    class FakeClient:
        api = FakeAPI()
        images = FakeImages()
        containers = FakeContainers()
        networks = FakeNetworks()

        def close(self) -> None:
            return None

    result = DockerApiBackend(docker_factory=lambda: FakeClient()).deploy(rebuilt, plan)

    assert result.succeeded, result.message
    removes = [i for i, call in enumerate(calls) if call.startswith("container.remove:")]
    network_remove = calls.index("network.remove")
    network_create = calls.index("network.create")
    creates = [i for i, call in enumerate(calls) if call.startswith("container.create:")]
    # 顺序锁：所有旧容器删除 < 网络 remove < 网络 create < 所有新容器创建。
    assert max(removes) < network_remove < network_create < min(creates)


def test_docker_api_fails_fast_on_blocked_network_before_removing_containers(tmp_path: Path) -> None:
    """IPAM 不匹配且有不在本轮删除计划内的 endpoint（外部容器 / KEEP 容器）
    → 在删除任何容器之前快速失败。"""

    state = build_hkg1_example_state()
    state_dict = state.model_dump(mode="json")
    state_dict["runtime"]["underlay"]["gateway"] = "10.254.42.254"
    rebuilt = state.__class__.model_validate(state_dict)
    plan = build_container_plan(rebuilt, _matching_observed(state, tmp_path), rendered_dir=tmp_path)

    class FakeImages:
        def build(self, **kwargs: object) -> tuple[object, list[object]]:
            return object(), []

        def get(self, image: str) -> object:
            return object()

    class FakeContainers:
        def get(self, name: str) -> object:
            raise AssertionError("must not touch containers when network is blocked")

    class FakeNetwork:
        attrs = {
            "IPAM": {"Config": [{"Subnet": "10.254.42.0/24", "Gateway": "10.254.42.1"}]},
            "Containers": {"0": {"Name": "user-unrelated-container"}},
        }

        def reload(self) -> None:
            return None

        def remove(self) -> None:
            raise AssertionError("must not remove a network with foreign endpoints")

    class FakeNetworks:
        def list(self, names: list[str]) -> list[FakeNetwork]:
            return [FakeNetwork()]

    class FakeClient:
        api = None
        images = FakeImages()
        containers = FakeContainers()
        networks = FakeNetworks()

        def close(self) -> None:
            return None

    result = DockerApiBackend(docker_factory=lambda: FakeClient()).deploy(rebuilt, plan)

    assert not result.succeeded
    assert "user-unrelated-container" in result.message
    assert result.removed_containers == []
    assert result.started_containers == []


def test_apply_executor_forwards_plan_to_docker_api(tmp_path: Path) -> None:
    """executor 是纯转发层：state / 计划原样交给 docker-api 后端。"""

    state = build_hkg1_example_state()
    plan = _plan_all_keep(state, tmp_path)
    captured: list[tuple[object, object]] = []

    class _FakeDockerApi:
        def deploy(self, deploy_state, container_plan):
            captured.append((deploy_state, container_plan))
            from agent.apply.docker_api import DockerApiResult

            return DockerApiResult(
                succeeded=True,
                project_name=container_plan.project_name,
                network="net",
            )

    executor = ApplyExecutor(docker_api=_FakeDockerApi())
    result = executor.deploy(state=state, container_plan=plan)

    assert result.succeeded
    assert result.backend == "docker-api"
    assert captured == [(state, plan)]


def test_writer_executes_plan_deletes_and_reports_them(tmp_path: Path) -> None:
    """计划-执行-上报同源（review 缺陷 A 回归锁）。

    受管范围内的孤儿文件必须出现在计划的 delete 动作里、被真实删除，
    且执行结果（上报数据源）如实包含该 delete——不再有"磁盘删了、
    上报里没有"的审计失真。
    """

    state = build_hkg1_example_state()
    bundle = render_state(state)

    orphan = tmp_path / "wireguard" / "ghost-iface.conf"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_text("[Interface]\n", encoding="utf-8")

    plan = build_file_plan(bundle.files, tmp_path, prune=True)
    assert any(
        action.action == "delete" and action.path == "wireguard/ghost-iface.conf"
        for action in plan.actions
    )

    outcome = write_rendered_bundle(bundle, tmp_path, file_plan=plan)

    assert not orphan.exists()
    deletes = [item for item in outcome.applied if item.action == "delete"]
    assert [item.path for item in deletes] == ["wireguard/ghost-iface.conf"]
    assert outcome.summary.delete == 1


@pytest.mark.skipif(os.name == "nt", reason="NTFS 无 POSIX 可执行位，chmod 不生效")
def test_writer_marks_rendered_shell_scripts_executable(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    bundle = render_state(state)

    _write_bundle(bundle, tmp_path)

    start_script = tmp_path / "scripts" / "wg" / "start-wg-gateway.sh"
    assert start_script.stat().st_mode & stat.S_IXUSR
    assert start_script.stat().st_mode & stat.S_IXGRP
    assert start_script.stat().st_mode & stat.S_IXOTH


def test_docker_api_builds_images_before_removing_existing_containers(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    calls: list[str] = []

    class FakeImages:
        def build(self, **kwargs: object) -> tuple[object, list[object]]:
            calls.append(f"build:{kwargs['tag']}")
            raise RuntimeError("build failed")

        def get(self, image: str) -> object:
            calls.append(f"get:{image}")
            return object()

    class FakeContainers:
        def get(self, name: str) -> object:
            calls.append(f"remove:{name}")
            raise AssertionError("old containers must not be removed before images build")

    class FakeClient:
        images = FakeImages()
        containers = FakeContainers()

        def close(self) -> None:
            calls.append("close")

    backend = DockerApiBackend(docker_factory=lambda: FakeClient())

    result = backend.deploy(state, _plan_all_create(state, tmp_path))

    assert not result.succeeded
    assert result.removed_containers == []
    assert calls[0].startswith("build:")
    assert not any(call.startswith("remove:") for call in calls)
