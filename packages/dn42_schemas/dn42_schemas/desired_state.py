from __future__ import annotations

"""描述单节点完整期望状态的顶层 schema。"""

from typing import Literal

from pydantic import Field, model_validator

from .base import StrictModel
from .dns import DnsSpec
from .enums import InterfaceKind, ServiceRole
from .network import InterfaceSpec, NodeSpec
from .routing import BgpSessionSpec, Bird2ConfigSpec
from .runtime import (
    BIRD_CONTROL_SOCKET_SOURCE,
    BIRD_CONTROL_SOCKET_TARGET,
    PortPublishSpec,
    RouterRuntimeSpec,
    RuntimeServiceSpec,
    VolumeMount,
)

# 历史 looking glass（已彻底移除）在 runtime 服务里用过的角色名；兼容垫片据此剥离
# 存量数据中的残留服务，使旧 base_template / generation 仍可加载。
_LEGACY_LOOKGLASS_ROLES = frozenset({"looking-glass-proxy", "looking-glass-frontend"})


class TemplateSetSpec(StrictModel):
    """声明当前节点应使用的模板集版本。

    这些字段决定 `dn42_templates` 和 `dn42_runtime` 在渲染时要选择哪一套模板目录。

    Attributes:
        bird: BIRD 模板集版本标识。
        wireguard: WireGuard 模板集版本标识。
        coredns: CoreDNS 模板集版本标识；为 `None` 时表示不使用对应模板集。
        docker: Docker 构建产物（router Dockerfile 等）模板集版本标识。
        scripts: 节点启动与应用脚本模板集版本标识。
    """

    bird: str = "config-bird2/v1"
    wireguard: str = "config-wireguard/v1"
    coredns: str | None = "config-coredns/v1"
    docker: str = "config-docker/v1"
    scripts: str = "config-scripts/v1"


class DesiredState(StrictModel):
    """单节点部署的完整声明式输入。

    这是基础包之间最核心的协议边界：

    - `dn42_schemas` 负责验证结构与引用关系
    - `dn42_templates` 负责把它渲染成配置与脚本
    - `dn42_runtime` 负责把相关 runtime 文件写出或比对

    Attributes:
        schema_version: 当前 schema 版本号，用于未来做向后兼容演进。
        generation: 当前期望状态的世代号；通常由上层控制面递增。
        node: 节点身份、ASN、前缀和 loopback 等基础信息。
        runtime: runtime 层部署方式、服务、underlay 和 Dockerfile 定义。
        bird: BIRD 模板所需的高层配置。
        interfaces: 节点应创建的接口列表，例如 dummy loopback 和 WireGuard。
        bgp_sessions: 节点应建立的 BGP 会话列表。
        dns: 节点本地 DNS 服务配置；为 `None` 时表示不生成 DNS 配置。
        templates: 当前节点渲染时使用的模板集版本选择。
    """

    schema_version: Literal["v1"] = "v1"
    generation: int = Field(ge=1)
    node: NodeSpec
    runtime: RouterRuntimeSpec
    bird: Bird2ConfigSpec = Field(default_factory=Bird2ConfigSpec)
    interfaces: list[InterfaceSpec] = Field(default_factory=list)
    bgp_sessions: list[BgpSessionSpec] = Field(default_factory=list)
    dns: DnsSpec | None = None
    templates: TemplateSetSpec = Field(default_factory=TemplateSetSpec)

    @model_validator(mode="before")
    @classmethod
    def _drop_legacy_lookglass(cls, data: object) -> object:
        """兼容垫片：吞掉历史数据里的 looking glass 残留（该功能已彻底移除）。

        存量 base_template / generation 快照可能仍带 ``lookglass`` 字段，以及
        ``looking-glass-*`` 角色的 runtime 服务。StrictModel 禁止多余字段 / 未知枚举，
        故在校验前剥掉它们：旧数据照常加载，且 agent 收敛时会把对应 LG 容器一并拆除
        （desired 不再含这些服务）。全 fleet 数据自然蜕掉 lookglass 后，本垫片可移除。
        """

        if not isinstance(data, dict):
            return data
        runtime = data.get("runtime")
        services = runtime.get("services") if isinstance(runtime, dict) else None
        has_lg_service = isinstance(services, list) and any(
            isinstance(svc, dict) and svc.get("role") in _LEGACY_LOOKGLASS_ROLES for svc in services
        )
        if "lookglass" not in data and not has_lg_service:
            return data
        cleaned = dict(data)
        cleaned.pop("lookglass", None)
        if has_lg_service:
            new_runtime = dict(runtime)
            new_runtime["services"] = [
                svc
                for svc in services
                if not (isinstance(svc, dict) and svc.get("role") in _LEGACY_LOOKGLASS_ROLES)
            ]
            cleaned["runtime"] = new_runtime
        return cleaned

    @model_validator(mode="after")
    def validate_references(self) -> "DesiredState":
        """校验跨字段引用关系并归一化 runtime（注入 bird 控制 socket 挂载、wg 端口发布）。"""

        interface_names = [interface.name for interface in self.interfaces]
        if len(interface_names) != len(set(interface_names)):
            raise ValueError("interface names must be unique")

        session_names = [session.name for session in self.bgp_sessions]
        if len(session_names) != len(set(session_names)):
            raise ValueError("BGP session names must be unique")

        known_interfaces = set(interface_names)
        missing = sorted(
            session.interface
            for session in self.bgp_sessions
            if session.interface and session.interface not in known_interfaces
        )
        if missing:
            raise ValueError(f"BGP sessions reference missing interfaces: {', '.join(missing)}")

        interface_to_asns: dict[str, set[int]] = {}
        for session in self.bgp_sessions:
            if session.interface and session.enabled and not session.is_internal(self.node.asn):
                interface_to_asns.setdefault(session.interface, set()).add(session.remote_asn)
        invalid_interfaces = sorted(
            interface for interface, asns in interface_to_asns.items() if len(asns) > 1
        )
        if invalid_interfaces:
            raise ValueError(
                "wireguard interfaces must not carry multiple remote ASNs: "
                + ", ".join(invalid_interfaces)
            )

        topology = self.bird.internal_topology
        if topology and self.node.node_id not in topology.routers + topology.private_nodes:
            raise ValueError("internal topology must include the current node")

        return _normalize_bird_control_socket(_normalize_wireguard_port_publish_runtime(self))


def _normalize_wireguard_port_publish_runtime(state: DesiredState) -> DesiredState:
    listen_port_by_interface: dict[int, str] = {}
    duplicates: list[str] = []
    for interface in state.interfaces:
        if interface.kind != InterfaceKind.WIREGUARD or interface.listen_port is None:
            continue
        existing = listen_port_by_interface.get(interface.listen_port)
        if existing is not None:
            duplicates.append(f"{interface.listen_port} ({existing}, {interface.name})")
        else:
            listen_port_by_interface[interface.listen_port] = interface.name

    if duplicates:
        raise ValueError(
            "wireguard listen_port must be unique per node: " + ", ".join(sorted(duplicates))
        )

    listen_ports = sorted(listen_port_by_interface)
    if not listen_ports:
        return state

    port_range = state.runtime.wireguard_port_range
    if port_range is None:
        return state

    out_of_range = [
        f"{name}={port}"
        for port, name in listen_port_by_interface.items()
        if not port_range.contains(port)
    ]
    if out_of_range:
        raise ValueError(
            "wireguard listen_port must be inside runtime.wireguard_port_range "
            f"{port_range.start}-{port_range.end}: "
            + ", ".join(sorted(out_of_range))
        )

    managed_port = PortPublishSpec(
        host_ip=port_range.host_ip,
        host_port=port_range.effective_host_start,
        host_port_end=port_range.effective_host_end,
        container_port=port_range.start,
        container_port_end=port_range.end,
        protocol="udp",
    )
    managed_key = _port_publish_key(managed_port)

    services: list[RuntimeServiceSpec] = []
    changed = False
    for service in state.runtime.services:
        if service.role != ServiceRole.ROUTER_NETNS:
            services.append(service)
            continue

        if managed_key not in {_port_publish_key(port) for port in service.ports}:
            changed = True
            services.append(
                service.model_copy(
                    update={
                        "ports": sorted(
                            [*service.ports, managed_port],
                            key=lambda port: (
                                port.host_ip or "",
                                port.host_port or 0,
                                port.host_port_end or port.host_port or 0,
                                port.container_port,
                                port.container_port_end or port.container_port,
                                port.protocol,
                            ),
                        )
                    }
                )
            )
        else:
            services.append(service)

    if not changed:
        return state

    updated_runtime = RouterRuntimeSpec.model_validate(
        {
            **state.runtime.model_dump(mode="python"),
            "services": [service.model_dump(mode="python") for service in services],
        }
    )
    object.__setattr__(state, "runtime", updated_runtime)
    return state


def _port_publish_key(port: PortPublishSpec) -> tuple[str | None, int | None, int | None, int, int | None, str]:
    return (
        port.host_ip,
        port.host_port,
        port.host_port_end,
        port.container_port,
        port.container_port_end,
        port.protocol,
    )


def _normalize_bird_control_socket(state: DesiredState) -> DesiredState:
    """确保 bird-router 始终暴露 ``/run/bird`` 控制 socket（可写挂载）。

    路由采集（agent 在宿主直连 ``bird.ctl``）依赖该挂载。它是 bird-router 的**一等不变量**，
    无条件注入。已有 ``/run/bird`` 挂载时只校验其可写；没有 bird-router 服务（极简 /
    异常状态）则原样返回。
    """

    services = state.runtime.services
    bird = next((service for service in services if service.role == ServiceRole.BIRD_ROUTER), None)
    if bird is None:
        return state

    existing = next(
        (mount for mount in bird.volumes if mount.target == BIRD_CONTROL_SOCKET_TARGET), None
    )
    if existing is not None:
        if existing.readonly:
            raise ValueError(
                f"bird-router {BIRD_CONTROL_SOCKET_TARGET} control socket mount must be writable"
            )
        return state

    updated_services = [
        service.model_copy(
            update={
                "volumes": [
                    *service.volumes,
                    VolumeMount(
                        source=BIRD_CONTROL_SOCKET_SOURCE,
                        target=BIRD_CONTROL_SOCKET_TARGET,
                        readonly=False,
                    ),
                ]
            }
        )
        if service.name == bird.name
        else service
        for service in services
    ]

    updated_runtime = RouterRuntimeSpec.model_validate(
        {
            **state.runtime.model_dump(mode="python"),
            "services": [service.model_dump(mode="python") for service in updated_services],
        }
    )
    object.__setattr__(state, "runtime", updated_runtime)
    return state
