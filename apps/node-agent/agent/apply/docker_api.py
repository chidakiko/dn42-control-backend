from __future__ import annotations

"""通过 Python Docker SDK 直接调用 Docker Engine 的部署后端。

这是 agent 唯一的部署路径：容器定义以 `ContainerDefinition.payload`
（决策层产出的最终参数集）直达 Engine API，不渲染、不依赖任何编排文件。

**执行端不做决策**：哪些容器需要 (re)create / remove、容器长什么样，
完全由传入的 `ContainerPlan` 决定（决策在 `agent.planner`，含内容寻址
判定、依赖传播与孤儿清理）；本模块只负责把计划忠实落地：

- 网络管理：`plan_underlay_network`（只读预检）+ `apply_underlay_network_plan`
  （容器释放 endpoint 之后落地 create/recreate）
- 镜像准备：`prepare_image`（按定义内存构建 / 拉取，只为 (re)create 服务）
- payload 物化：`binds_mapping` / `port_bindings_from_payload` 等纯转换
- 编排：`DockerApiBackend.deploy`
"""

import importlib
import io
from dataclasses import dataclass, field
from typing import Any, Callable

from dn42_common import network_labels
from dn42_schemas import DesiredState, RuntimeServiceSpec

from ..planner.container_plan import ContainerAction, ContainerPlan
from ..planner.definition import ContainerDefinition


DockerFactory = Callable[[], Any]


@dataclass(frozen=True, slots=True)
class DockerApiResult:
    """Docker API 部署的结构化结果。"""

    succeeded: bool
    project_name: str
    network: str
    started_containers: list[str] = field(default_factory=list)
    removed_containers: list[str] = field(default_factory=list)
    built_images: list[str] = field(default_factory=list)
    pulled_images: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        """以 dict 形式返回，兼容历史 summary 输出。"""

        return {
            "backend": "docker-api",
            "succeeded": self.succeeded,
            "project_name": self.project_name,
            "network": self.network,
            "started_containers": list(self.started_containers),
            "removed_containers": list(self.removed_containers),
            "built_images": list(self.built_images),
            "pulled_images": list(self.pulled_images),
            "stdout": "",
            "stderr": self.message if not self.succeeded else "",
        }


def _default_docker_factory() -> Any:
    docker_sdk = importlib.import_module("docker")
    return docker_sdk.from_env()


class DockerApiBackend:
    """Docker Engine API 部署后端。"""

    def __init__(self, docker_factory: DockerFactory | None = None) -> None:
        self._docker_factory: DockerFactory = docker_factory or _default_docker_factory

    def deploy(self, state: DesiredState, container_plan: ContainerPlan) -> DockerApiResult:
        """**严格按 `container_plan`** 把容器收敛到期望态。

        KEEP 的容器一概不碰；REMOVE（孤儿）先清；CREATE/RECREATE 的容器
        按拓扑序、照 step 携带的 `ContainerDefinition` 重建。
        """

        project = container_plan.project_name
        network = f"{project}_{state.runtime.underlay.name}"
        ordered_services = topologically_sorted_services(state)
        step_by_service = {
            step.service_name: step for step in container_plan.steps if step.service_name
        }
        recreate_steps = [
            step_by_service[service.name]
            for service in ordered_services
            if service.name in step_by_service
            and step_by_service[service.name].action
            in (ContainerAction.CREATE, ContainerAction.RECREATE)
        ]
        orphan_steps = container_plan.to_remove

        client = self._docker_factory()
        removed: list[str] = []
        started: list[str] = []
        built: list[str] = []
        pulled: list[str] = []
        try:
            # 只为计划要 (re)create 的服务准备镜像；KEEP 服务的镜像本来就在用。
            image_names: dict[str, str] = {}
            for step in recreate_steps:
                assert step.definition is not None  # CREATE/RECREATE 必带定义
                image_name, image_action = prepare_image(client, step.definition)
                image_names[step.container_name] = image_name
                if image_action == "built":
                    built.append(image_name)
                elif image_action == "pulled":
                    pulled.append(image_name)

            # 只读预检：网络要 create/recreate/keep？IPAM 不匹配而挂载的
            # endpoint 又不会在本轮被删除时，在动任何容器之前快速失败。
            removable_names = {step.container_name for step in orphan_steps} | {
                step.container_name for step in recreate_steps
            }
            network_plan = plan_underlay_network(
                client,
                network_name=network,
                subnet=state.runtime.underlay.subnet,
                gateway=state.runtime.underlay.gateway,
                removable_container_names=removable_names,
                ipv6_subnet=state.runtime.underlay.ipv6_subnet,
                ipv6_gateway=state.runtime.underlay.ipv6_gateway,
            )
            if not network_plan["succeeded"]:
                return DockerApiResult(
                    succeeded=False,
                    project_name=project,
                    network=network,
                    built_images=built,
                    pulled_images=pulled,
                    message=str(network_plan["message"]),
                )

            # 孤儿先清（它们不在拓扑里，也不会被任何期望容器引用）。
            removed.extend(_remove_containers(client, [step.container_name for step in orphan_steps]))
            # 再按拓扑逆序删除要重建的旧容器，避免 network_mode 引用悬空。
            removed.extend(
                _remove_containers(
                    client, [step.container_name for step in reversed(recreate_steps)]
                )
            )

            # endpoint 已随容器删除而释放，现在才允许 remove/create 网络。
            ensured = apply_underlay_network_plan(
                client,
                action=str(network_plan["action"]),
                network_name=network,
                subnet=state.runtime.underlay.subnet,
                gateway=state.runtime.underlay.gateway,
                ipv6_subnet=state.runtime.underlay.ipv6_subnet,
                ipv6_gateway=state.runtime.underlay.ipv6_gateway,
            )
            if not ensured["succeeded"]:
                return DockerApiResult(
                    succeeded=False,
                    project_name=project,
                    network=network,
                    removed_containers=removed,
                    built_images=built,
                    pulled_images=pulled,
                    message=str(ensured["message"]),
                )

            api = client.api
            for step in recreate_steps:
                definition = step.definition
                assert definition is not None
                container = api.create_container(
                    **build_create_kwargs(api, definition, image_names[step.container_name])
                )
                api.start(container=container["Id"])
                started.append(definition.container_name)

            return DockerApiResult(
                succeeded=True,
                project_name=project,
                network=network,
                started_containers=started,
                removed_containers=removed,
                built_images=built,
                pulled_images=pulled,
            )
        except Exception as exc:
            return DockerApiResult(
                succeeded=False,
                project_name=project,
                network=network,
                started_containers=started,
                removed_containers=removed,
                built_images=built,
                pulled_images=pulled,
                message=str(exc),
            )
        finally:
            try:
                client.close()
            except Exception:
                pass


def topologically_sorted_services(state: DesiredState) -> list[RuntimeServiceSpec]:
    """按 `depends_on` 做拓扑排序，未启用服务被忽略。"""

    services = [service for service in state.runtime.services if service.enabled]
    by_name = {service.name: service for service in services}
    pending = {
        service.name: {dep for dep in service.depends_on if dep in by_name}
        for service in services
    }
    ordered: list[RuntimeServiceSpec] = []
    while pending:
        ready = sorted(name for name, deps in pending.items() if not deps)
        if not ready:
            raise ValueError("runtime service graph contains a dependency cycle")
        for name in ready:
            ordered.append(by_name[name])
            pending.pop(name)
        for deps in pending.values():
            deps.difference_update(ready)
    return ordered


def _remove_containers(client: Any, names: list[str]) -> list[str]:
    removed: list[str] = []
    for name in names:
        try:
            container = client.containers.get(name)
        except Exception:
            continue
        container.remove(force=True)
        removed.append(name)
    return removed


def prepare_image(client: Any, definition: ContainerDefinition) -> tuple[str, str]:
    """按定义准备镜像。

    - `image.build`：用定义携带的 Dockerfile 内容经 `fileobj` 内存构建并打 tag；
    - `image.ref` 且本地存在：直接复用；
    - 否则：拉取。
    """

    image = definition.payload["image"]
    build = image.get("build")
    if build is not None:
        if definition.dockerfile is None:
            raise ValueError(
                f"container {definition.container_name} build definition is missing dockerfile content"
            )
        client.images.build(
            fileobj=io.BytesIO(definition.dockerfile.encode("utf-8")),
            tag=build["tag"],
            target=build["target"],
            buildargs=build["args"] or None,
            rm=True,
        )
        return build["tag"], "built"
    ref = image["ref"]
    try:
        client.images.get(ref)
        return ref, "existing"
    except Exception:
        client.images.pull(ref)
        return ref, "pulled"


def plan_underlay_network(
    client: Any,
    *,
    network_name: str,
    subnet: str,
    gateway: str,
    removable_container_names: set[str],
    ipv6_subnet: str | None = None,
    ipv6_gateway: str | None = None,
) -> dict[str, object]:
    """只读预检 underlay bridge network，决定 keep / create / recreate。

    IPAM 不匹配时，仅当所有挂载 endpoint 都会在本轮被删除
    （`removable_container_names`，即孤儿 + RECREATE 旧容器）才允许重建；
    残留 endpoint 意味着要么是外部容器（绝不能动），要么是计划判 KEEP 的
    容器（说明网络被手工改过、与容器哈希脱节）——两种都应快速失败，
    而不是等 `network.remove()` 在删容器后才报 active endpoints。
    """

    existing = client.networks.list(names=[network_name])
    if not existing:
        return {"succeeded": True, "action": "create", "message": ""}

    network = existing[0]
    network.reload()
    if _network_matches(
        network,
        subnet=subnet,
        gateway=gateway,
        ipv6_subnet=ipv6_subnet,
        ipv6_gateway=ipv6_gateway,
    ):
        return {"succeeded": True, "action": "keep", "message": "using existing underlay network"}

    attached = _attached_container_names(network)
    blockers = sorted(name for name in attached if name not in removable_container_names)
    if blockers:
        return {
            "succeeded": False,
            "action": "recreate",
            "message": (
                f"existing network {network_name} has incompatible IPAM config and "
                f"endpoints not scheduled for removal this run: {', '.join(blockers)}"
            ),
        }
    return {"succeeded": True, "action": "recreate", "message": ""}


def apply_underlay_network_plan(
    client: Any,
    *,
    action: str,
    network_name: str,
    subnet: str,
    gateway: str,
    ipv6_subnet: str | None = None,
    ipv6_gateway: str | None = None,
) -> dict[str, object]:
    """落地预检结论。必须在 (re)create / 孤儿容器删除**之后**调用：

    recreate 依赖 endpoint 已随容器删除而全部释放。
    """

    if action == "keep":
        return {"succeeded": True, "message": "using existing underlay network"}

    if action == "recreate":
        existing = client.networks.list(names=[network_name])
        if existing:
            network = existing[0]
            network.reload()
            attached = _attached_container_names(network)
            if attached:
                # 预检之后世界变了（并发起容器等），拒绝硬拆。
                return {
                    "succeeded": False,
                    "message": (
                        f"cannot recreate network {network_name}: endpoints still "
                        f"attached after container removal: {', '.join(sorted(attached))}"
                    ),
                }
            network.remove()

    docker_types = importlib.import_module("docker.types")
    pools = [docker_types.IPAMPool(subnet=subnet, gateway=gateway)]
    create_kwargs: dict[str, object] = {}
    if ipv6_subnet:
        # 启用 IPv6：容器获 IPv6 + 默认路由；daemon ip6tables=true 时 Docker 自动建 NAT66，
        # 容器经宿主公网 IPv6 出网（拨 IPv6-only 对端 endpoint）。
        pools.append(docker_types.IPAMPool(subnet=ipv6_subnet, gateway=ipv6_gateway))
        create_kwargs["enable_ipv6"] = True
    client.networks.create(
        network_name,
        driver="bridge",
        ipam=docker_types.IPAMConfig(pool_configs=pools),
        labels=network_labels(),
        **create_kwargs,
    )
    return {"succeeded": True, "message": "created underlay network"}


def _network_matches(
    network: Any,
    *,
    subnet: str,
    gateway: str,
    ipv6_subnet: str | None = None,
    ipv6_gateway: str | None = None,
) -> bool:
    config = network.attrs.get("IPAM", {}).get("Config", [])
    v4_ok = False
    v6_ok = ipv6_subnet is None  # 不要 IPv6 时 v6 维度天然满足
    for candidate in config:
        candidate_subnet = candidate.get("Subnet")
        if not candidate_subnet:
            continue
        if ":" in candidate_subnet:
            if (
                ipv6_subnet is not None
                and candidate_subnet == ipv6_subnet
                and candidate.get("Gateway") == ipv6_gateway
            ):
                v6_ok = True
        elif candidate_subnet == subnet and candidate.get("Gateway") == gateway:
            v4_ok = True
    return v4_ok and v6_ok
    return False


def _attached_container_names(network: Any) -> set[str]:
    containers = network.attrs.get("Containers") or {}
    return {container.get("Name", "") for container in containers.values() if container.get("Name")}


# ----- payload -> docker SDK 参数的纯转换（不做任何决策） -----


def build_create_kwargs(api: Any, definition: ContainerDefinition, image_name: str) -> dict[str, Any]:
    """把 `ContainerDefinition.payload` 物化成 `create_container` 参数。"""

    payload = definition.payload
    bindings = port_bindings_from_payload(payload)
    network = payload["network"]
    host_config = api.create_host_config(
        binds=binds_mapping(payload) or None,
        port_bindings=bindings or None,
        network_mode=network.get("mode"),
        cap_add=payload["cap_add"] or None,
        devices=payload["devices"] or None,
        sysctls=payload["sysctls"] or None,
        restart_policy={"Name": payload["restart_policy"]},
        init=payload["init"],
    )
    create_kwargs: dict[str, Any] = {
        "image": image_name,
        "name": definition.container_name,
        "command": payload["command"] or None,
        "environment": payload["environment"] or None,
        "host_config": host_config,
        "labels": definition.labels,
    }
    if payload["binds"]:
        create_kwargs["volumes"] = [entry["target"] for entry in payload["binds"]]
    if bindings:
        create_kwargs["ports"] = exposed_ports_from_payload(payload)
    healthcheck = healthcheck_config_from_payload(payload)
    if healthcheck is not None:
        create_kwargs["healthcheck"] = healthcheck
    attach = network.get("attach")
    if attach is not None:
        endpoint = api.create_endpoint_config(
            ipv4_address=attach["ipv4_address"],
            aliases=attach["aliases"],
        )
        create_kwargs["networking_config"] = api.create_networking_config(
            {attach["network"]: endpoint}
        )
    return create_kwargs


def binds_mapping(payload: dict[str, Any]) -> dict[str, dict[str, str]]:
    """payload 的 binds 条目 -> docker SDK binds 映射。"""

    return {
        entry["source"]: {"bind": entry["target"], "mode": entry["mode"]}
        for entry in payload["binds"]
    }


def port_bindings_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """payload 的逐端口条目 -> docker SDK port_bindings 字典。"""

    bindings: dict[str, Any] = {}
    for entry in payload["ports"]:
        if entry["host_port"] is None:
            continue
        key = f"{entry['container_port']}/{entry['protocol']}"
        if entry["host_ip"]:
            bindings[key] = (entry["host_ip"], entry["host_port"])
        else:
            bindings[key] = entry["host_port"]
    return bindings


def exposed_ports_from_payload(payload: dict[str, Any]) -> list[tuple[int, str]]:
    """payload 的逐端口条目 -> create_container 的 ExposedPorts 声明。"""

    ports: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for entry in payload["ports"]:
        key = (entry["container_port"], entry["protocol"])
        if key in seen:
            continue
        seen.add(key)
        ports.append(key)
    return ports


def healthcheck_config_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """payload 的秒级 healthcheck -> Docker API 期望的纳秒粒度 dict。"""

    healthcheck = payload["healthcheck"]
    if healthcheck is None:
        return None
    return {
        "Test": healthcheck["test"],
        "Interval": healthcheck["interval_seconds"] * 1_000_000_000,
        "Timeout": healthcheck["timeout_seconds"] * 1_000_000_000,
        "Retries": healthcheck["retries"],
        "StartPeriod": healthcheck["start_period_seconds"] * 1_000_000_000,
    }


__all__ = [
    "DockerApiBackend",
    "DockerApiResult",
    "DockerFactory",
    "binds_mapping",
    "build_create_kwargs",
    "apply_underlay_network_plan",
    "plan_underlay_network",
    "exposed_ports_from_payload",
    "healthcheck_config_from_payload",
    "port_bindings_from_payload",
    "prepare_image",
    "topologically_sorted_services",
]
