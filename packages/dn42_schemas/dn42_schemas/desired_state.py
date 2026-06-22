from __future__ import annotations

"""描述单节点完整期望状态的顶层 schema。"""

import ipaddress
from typing import Literal

from pydantic import Field, model_validator

from .base import StrictModel
from .dns import DnsSpec
from .enums import InterfaceKind, ServiceRole
from .network import InterfaceSpec, NodeSpec
from .routing import BgpSessionSpec, Bird2ConfigSpec, DummyInterfaceSpec
from .runtime import (
    BIRD_CONTROL_SOCKET_SOURCE,
    BIRD_CONTROL_SOCKET_TARGET,
    DNS_ANYCAST_INTERFACE,
    DNS_CONFIG_SOURCE,
    DNS_CONFIG_TARGET,
    DNS_SERVICE_IMAGE,
    DNS_SERVICE_NAME,
    PortPublishSpec,
    RouterRuntimeSpec,
    RuntimeServiceSpec,
    VolumeMount,
)


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

        return _normalize_dns_anycast(
            _normalize_dns_runtime(
                _normalize_bird_control_socket(_normalize_wireguard_port_publish_runtime(self))
            )
        )


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
            f"{port_range.start}-{port_range.end}: " + ", ".join(sorted(out_of_range))
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


def _port_publish_key(
    port: PortPublishSpec,
) -> tuple[str | None, int | None, int | None, int, int | None, str]:
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


def _normalize_dns_runtime(state: DesiredState) -> DesiredState:
    """启用 DNS 时注入框架托管的 CoreDNS runtime 服务（"分配组即启用"）。

    节点分配了有效 DNS 组后，materializer 把组的配置填进 ``dns``；此处据此确保 runtime 里
    有 ``dns`` 角色服务（CoreDNS：与 router-netns 共享 netns、挂渲染出的 ``coredns/`` 配置目录），
    agent 收敛即部署。``dns`` 为 None / 未启用时**剥掉**框架托管的 CoreDNS 服务——desired 不含
    该服务，agent 一并拆除 CoreDNS（dns 服务由本函数单源管理，故按 dns 角色识别即可剥离）。
    已有 dns 角色服务则原样保留（去重）；无 router-netns 服务（极简 / 异常状态）则跳过注入。
    """

    dns = state.dns
    services = state.runtime.services
    has_dns_service = any(service.role == ServiceRole.DNS for service in services)

    if dns is None or not dns.enabled:
        if not has_dns_service:
            return state
        kept = [service for service in services if service.role != ServiceRole.DNS]
        updated_runtime = RouterRuntimeSpec.model_validate(
            {
                **state.runtime.model_dump(mode="python"),
                "services": [service.model_dump(mode="python") for service in kept],
            }
        )
        object.__setattr__(state, "runtime", updated_runtime)
        return state

    if has_dns_service:
        return state
    router_netns = next(
        (service for service in services if service.role == ServiceRole.ROUTER_NETNS), None
    )
    if router_netns is None:
        return state

    depends = [router_netns.name]
    wg_gateway = next(
        (service for service in services if service.role == ServiceRole.WG_GATEWAY), None
    )
    if wg_gateway is not None:
        depends.append(wg_gateway.name)

    dns_service = RuntimeServiceSpec(
        name=DNS_SERVICE_NAME,
        role=ServiceRole.DNS,
        image=DNS_SERVICE_IMAGE,
        command=["-conf", f"{DNS_CONFIG_TARGET}/Corefile"],
        network_mode=f"service:{router_netns.name}",
        volumes=[VolumeMount(source=DNS_CONFIG_SOURCE, target=DNS_CONFIG_TARGET)],
        depends_on=depends,
    )

    updated_runtime = RouterRuntimeSpec.model_validate(
        {
            **state.runtime.model_dump(mode="python"),
            "services": [
                *(service.model_dump(mode="python") for service in services),
                dns_service.model_dump(mode="python"),
            ],
        }
    )
    object.__setattr__(state, "runtime", updated_runtime)
    return state


def _host_cidr(address: str) -> str:
    """裸 IP → 主机 CIDR（v4 → ``/32``、v6 → ``/128``）；已带前缀则原样返回。"""

    if "/" in address:
        return address
    return f"{address}/{ipaddress.ip_address(address).max_prefixlen}"


def _normalize_dns_anycast(state: DesiredState) -> DesiredState:
    """据 ``dns.bind_addresses`` 注入/剥离框架托管的 DNS 任播接口（"分配组即启用"的网络面）。

    节点订阅 DNS 组后，组的服务地址 ``dns.bind_addresses`` 是 DNS 服务地址的**唯一真源**。
    这里把它派生成两样并以"是否启用 DNS"为开关，与 ``_normalize_dns_runtime`` 注入 CoreDNS
    服务完全同构：

    - 启用且有 bind 地址 ⇒ 合成一条 dummy 接口 ``dns-anycast`` 承载这些地址（v4 → ``/32``、
      v6 → ``/128``），并登记为 ``track_service`` dummy ⇒ BIRD direct_anycast 起源对应前缀，
      任播地址进入 BGP。多节点订阅同一组拿到相同地址 ⇒ anycast / 任拨。
    - dns 为 None / 未启用 / 无 bind 地址 ⇒ **剥掉**这条托管接口与登记项：地址既不挂内核也
      不宣告，未提供 DNS 的节点不会黑洞任播流量。

    托管接口按保留名 ``dns-anycast`` 单源识别：每次先剔除同名残留再按需重建，故重复校验
    幂等（serialized desired 回灌 agent 再校验不会翻倍）。
    """

    dns = state.dns
    enabled = dns is not None and dns.enabled and bool(dns.bind_addresses)

    # 先剔除托管接口/登记项的任何残留（保留名单源），再按需重建 ⇒ 幂等。
    interfaces = [iface for iface in state.interfaces if iface.name != DNS_ANYCAST_INTERFACE]
    dummy_interfaces = {
        name: spec
        for name, spec in state.bird.dummy_interfaces.items()
        if name != DNS_ANYCAST_INTERFACE
    }

    if enabled:
        interfaces.append(
            InterfaceSpec(
                name=DNS_ANYCAST_INTERFACE,
                kind=InterfaceKind.DUMMY,
                mtu=None,
                addresses=[_host_cidr(address) for address in dns.bind_addresses],
            )
        )
        dummy_interfaces[DNS_ANYCAST_INTERFACE] = DummyInterfaceSpec(
            ifname=DNS_ANYCAST_INTERFACE, track_service=True
        )

    if interfaces != list(state.interfaces):
        object.__setattr__(state, "interfaces", interfaces)
    if dummy_interfaces != dict(state.bird.dummy_interfaces):
        updated_bird = Bird2ConfigSpec.model_validate(
            {
                **state.bird.model_dump(mode="python"),
                "dummy_interfaces": {
                    name: spec.model_dump(mode="python") for name, spec in dummy_interfaces.items()
                },
            }
        )
        object.__setattr__(state, "bird", updated_bird)
    return state
