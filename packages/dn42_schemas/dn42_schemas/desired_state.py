from __future__ import annotations

"""描述单节点完整期望状态的顶层 schema。"""

from typing import Literal, cast

from pydantic import Field, model_validator

from .base import StrictModel
from .dns import DnsSpec
from .enums import InterfaceKind, ServiceRole
from .lookglass import LookglassSpec
from .network import InterfaceSpec, NodeSpec
from .routing import BgpSessionSpec, Bird2ConfigSpec
from .runtime import PortPublishSpec, RouterRuntimeSpec, RuntimeServiceSpec, VolumeMount


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
        lookglass: lookglass 侧车配置；启用后会在校验阶段自动归一化到 runtime 服务列表。
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
    lookglass: LookglassSpec | None = None
    templates: TemplateSetSpec = Field(default_factory=TemplateSetSpec)

    @model_validator(mode="after")
    def validate_references(self) -> "DesiredState":
        """校验跨字段引用关系并在需要时注入 lookglass runtime 服务。"""

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

        return _normalize_lookglass_runtime(_normalize_wireguard_port_publish_runtime(self))


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


def _normalize_lookglass_runtime(state: DesiredState) -> DesiredState:
    lookglass = state.lookglass
    if lookglass is None or not lookglass.enabled:
        return state

    services = _strip_framework_managed_lookglass_services(state.runtime.services, lookglass)

    router_netns = next(
        service for service in services if service.role == ServiceRole.ROUTER_NETNS
    )
    bird_router = next(service for service in services if service.role == ServiceRole.BIRD_ROUTER)

    services = _with_shared_bird_socket_mount(services, bird_router.name, lookglass)
    services.extend(_build_lookglass_services(lookglass, router_netns.name, bird_router.name))

    updated_runtime = RouterRuntimeSpec.model_validate(
        {
            **state.runtime.model_dump(mode="python"),
            "services": [service.model_dump(mode="python") for service in services],
        }
    )
    object.__setattr__(state, "runtime", updated_runtime)
    return state


def _strip_framework_managed_lookglass_services(
    services: list[RuntimeServiceSpec],
    lookglass: LookglassSpec,
) -> list[RuntimeServiceSpec]:
    stripped: list[RuntimeServiceSpec] = []
    invalid_overlap: list[str] = []

    expected_names = {
        ServiceRole.LOOKING_GLASS_PROXY: lookglass.proxy_service_name,
        ServiceRole.LOOKING_GLASS_FRONTEND: lookglass.frontend_service_name,
    }

    for service in services:
        expected_name = expected_names.get(service.role)
        if expected_name is None:
            stripped.append(service)
            continue
        if service.name != expected_name:
            invalid_overlap.append(service.role.value)

    if invalid_overlap:
        raise ValueError(
            "runtime services must not define lookglass roles directly when lookglass is configured: "
            + ", ".join(sorted(set(invalid_overlap)))
        )

    return [service for service in stripped if service.role not in expected_names]


def _with_shared_bird_socket_mount(
    services: list[RuntimeServiceSpec],
    bird_router_name: str,
    lookglass: LookglassSpec,
) -> list[RuntimeServiceSpec]:
    updated_services: list[RuntimeServiceSpec] = []
    for service in services:
        if service.name != bird_router_name:
            updated_services.append(service)
            continue

        shared_mount = next((mount for mount in service.volumes if mount.target == "/run/bird"), None)
        if shared_mount is not None:
            if shared_mount.readonly:
                raise ValueError("lookglass requires the bird-router /run/bird mount to be writable")
            updated_services.append(service)
            continue

        updated_services.append(
            service.model_copy(
                update={
                    "volumes": [
                        *service.volumes,
                        VolumeMount(
                            source=lookglass.shared_socket_dir,
                            target="/run/bird",
                            readonly=False,
                        ),
                    ]
                }
            )
        )
    return updated_services


def _build_lookglass_services(
    lookglass: LookglassSpec,
    router_netns_name: str,
    bird_router_name: str,
) -> list[RuntimeServiceSpec]:
    servers = lookglass.servers or [router_netns_name]
    services = [
        RuntimeServiceSpec(
            name=lookglass.proxy_service_name,
            role=ServiceRole.LOOKING_GLASS_PROXY,
            image=lookglass.proxy_image,
            network_mode=f"service:{router_netns_name}",
            environment={
                key: value
                for key, value in {
                    "BIRD_SOCKET": "/run/bird/bird.ctl",
                    "BIRDLG_LISTEN": f"0.0.0.0:{lookglass.proxy_port}",
                    "ALLOWED_IPS": ",".join(lookglass.allowed_ips),
                }.items()
                if value
            },
            volumes=[
                VolumeMount(
                    source=lookglass.shared_socket_dir,
                    target="/run/bird",
                    readonly=False,
                )
            ],
            depends_on=[router_netns_name, bird_router_name],
        )
    ]

    if lookglass.frontend_enabled:
        services.append(
            RuntimeServiceSpec(
                name=lookglass.frontend_service_name,
                role=ServiceRole.LOOKING_GLASS_FRONTEND,
                image=lookglass.frontend_image,
                environment={
                    "BIRDLG_SERVERS": ",".join(servers),
                    "BIRDLG_DOMAIN": lookglass.domain,
                    "BIRDLG_LISTEN": f":{lookglass.frontend_port}",
                    "BIRDLG_PROXY_PORT": str(lookglass.proxy_port),
                    "BIRDLG_TITLE_BRAND": lookglass.title_brand,
                    "BIRDLG_NAVBAR_BRAND": lookglass.navbar_brand or lookglass.title_brand,
                    "BIRDLG_PROTOCOL_FILTER": ",".join(lookglass.protocol_filter),
                    "BIRDLG_WHOIS": lookglass.whois_command,
                    "BIRDLG_NET_SPECIFIC_MODE": lookglass.net_specific_mode,
                },
                ports=cast("list[PortPublishSpec]", lookglass.published_frontend_ports),
                depends_on=[lookglass.proxy_service_name],
            )
        )

    return services
