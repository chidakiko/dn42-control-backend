from __future__ import annotations

"""runtime 与容器部署相关的 schema。"""

from ipaddress import ip_address, ip_network
import re

from pydantic import Field, field_validator, model_validator

from .base import StrictModel
from .enums import ServiceRole

# BIRD 控制 socket（bird.ctl）运行目录的规范挂载——bird-router 的一等不变量。
# 宿主 agent 经此可写挂载直连 socket 采集路由（见 desired_state._normalize_bird_control_socket）。
# target 与 BIRD2 默认 socket 目录一致。
BIRD_CONTROL_SOCKET_SOURCE = "runtime/bird-run"
BIRD_CONTROL_SOCKET_TARGET = "/run/bird"


class VolumeMount(StrictModel):
    """运行时服务挂载的单个卷定义。

    Attributes:
        source: 宿主路径、命名卷名或相对路径来源。
        target: 容器内挂载目标路径。
        readonly: 是否以只读方式挂载。
    """

    source: str
    target: str
    readonly: bool = True


class HealthCheckSpec(StrictModel):
    """容器健康检查定义。

    Attributes:
        test: 健康检查命令数组。
        interval_seconds: 检查间隔秒数。
        timeout_seconds: 单次检查超时秒数。
        retries: 连续失败多少次后视为不健康。
        start_period_seconds: 容器启动后的宽限期秒数。
    """

    test: list[str]
    interval_seconds: int = Field(default=15, ge=1)
    timeout_seconds: int = Field(default=5, ge=1)
    retries: int = Field(default=5, ge=1)
    start_period_seconds: int = Field(default=10, ge=0)


class BuildSpec(StrictModel):
    """镜像构建参数定义。

    Dockerfile 内容由 ``runtime.router_dockerfile`` 参数在 agent 内生成并经
    Docker Engine API 内存构建——没有构建上下文目录、没有渲染的 Dockerfile
    文件，这里只声明多阶段 target 与 build args。

    Attributes:
        target: 多阶段构建时选择的 target 名。
        args: 传给 Docker build 的构建参数。
    """

    target: str | None = None
    args: dict[str, str] = Field(default_factory=dict)


class PortPublishSpec(StrictModel):
    """单个端口发布规则。

    Attributes:
        container_port: 容器内端口。
        host_port: 发布到宿主机的端口；为 `None` 时表示仅声明容器端口。
        protocol: 端口协议，目前支持 tcp、udp、sctp。
        host_ip: 绑定的宿主机地址；为 `None` 时由 runtime 默认行为决定。
    """

    container_port: int = Field(ge=1, le=65535)
    container_port_end: int | None = Field(default=None, ge=1, le=65535)
    host_port: int | None = Field(default=None, ge=1, le=65535)
    host_port_end: int | None = Field(default=None, ge=1, le=65535)
    protocol: str = "tcp"
    host_ip: str | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> "PortPublishSpec":
        if self.container_port_end is not None and self.container_port_end < self.container_port:
            raise ValueError("container_port_end must be greater than or equal to container_port")
        if self.host_port_end is not None and self.host_port is None:
            raise ValueError("host_port_end requires host_port")
        if self.host_port is not None and self.host_port_end is None and self.container_port_end is not None:
            object.__setattr__(
                self,
                "host_port_end",
                self.host_port + (self.container_port_end - self.container_port),
            )
        if self.host_port_end is not None and self.host_port_end > 65535:
            raise ValueError("host_port_end must be less than or equal to 65535")
        if self.host_port_end is not None and self.host_port_end < (self.host_port or 0):
            raise ValueError("host_port_end must be greater than or equal to host_port")
        if self.container_port_end is not None and self.host_port_end is not None and self.host_port is not None:
            container_size = self.container_port_end - self.container_port
            host_size = self.host_port_end - self.host_port
            if container_size != host_size:
                raise ValueError("host and container port ranges must have the same size")
        return self

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"tcp", "udp", "sctp"}:
            raise ValueError("port protocol must be tcp, udp, or sctp")
        return normalized

    @field_validator("host_ip")
    @classmethod
    def validate_host_ip(cls, value: str | None) -> str | None:
        if value is not None:
            ip_address(value)
        return value


class RouterDockerfileSpec(StrictModel):
    """路由器镜像 Dockerfile 模板参数。

    Attributes:
        base_image: 路由器镜像构建所基于的基础镜像。
        debian_mirror: Debian 包源镜像地址。
    """

    base_image: str = "debian:13-slim"
    debian_mirror: str = "deb.debian.org"


class RuntimeServiceSpec(StrictModel):
    """单个 runtime service 的声明式定义。

    Attributes:
        name: 服务名；在同一节点 deployment 中必须唯一。
        role: 服务语义角色，例如 router-netns、bird-router、rpki-cache。
        image: 直接使用的镜像名；与 `build` 互斥。
        build: 完整构建参数定义。
        ipv4_address: 显式指定在 underlay 中的 IPv4 地址。
        command: 容器启动命令数组。
        environment: 注入容器的环境变量字典。
        ports: 需要发布到宿主机的端口列表。
        network_mode: 容器网络模式，例如 `service:<name>`。
        cap_add: 额外 Linux capability 列表。
        devices: 需要透传到容器的设备列表。
        sysctls: 需要在容器创建时应用的 sysctl 键值。
        volumes: 挂载卷列表。
        depends_on: 启动依赖的其他服务名列表。
        healthcheck: 容器健康检查定义。
        enabled: 是否参与最终 runtime 渲染。
    """

    name: str
    role: ServiceRole
    image: str | None = None
    build: BuildSpec | None = None
    ipv4_address: str | None = None
    command: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    ports: list[PortPublishSpec] = Field(default_factory=list)
    network_mode: str | None = None
    cap_add: list[str] = Field(default_factory=list)
    devices: list[str] = Field(default_factory=list)
    sysctls: dict[str, str] = Field(default_factory=dict)
    volumes: list[VolumeMount] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    healthcheck: HealthCheckSpec | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def validate_build_fields(self) -> "RuntimeServiceSpec":
        if self.image and self.build:
            raise ValueError("runtime service cannot set both image and build")

        return self

    @field_validator("ports", mode="before")
    @classmethod
    def normalize_ports(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [
            _port_publish_spec_from_string(item) if isinstance(item, str) else item
            for item in value
        ]

    @field_validator("ipv4_address")
    @classmethod
    def validate_ipv4_address(cls, value: str | None) -> str | None:
        if value is not None:
            ip_address(value)
        return value


class UnderlayNetworkSpec(StrictModel):
    """节点 deployment 的 underlay 网络定义。

    Attributes:
        name: underlay 网络名。
        subnet: underlay 子网 CIDR。
        gateway: underlay 网关地址。
    """

    name: str = "router_underlay"
    subnet: str
    gateway: str

    @field_validator("subnet")
    @classmethod
    def validate_subnet(cls, value: str) -> str:
        ip_network(value, strict=False)
        return value


class WireGuardPortRangeSpec(StrictModel):
    """WireGuard UDP ports allowed and published for a node."""

    start: int = Field(ge=1, le=65535)
    end: int = Field(ge=1, le=65535)
    host_start: int | None = Field(default=None, ge=1, le=65535)
    host_ip: str | None = None

    @model_validator(mode="after")
    def validate_range(self) -> "WireGuardPortRangeSpec":
        if self.end < self.start:
            raise ValueError("wireguard port range end must be greater than or equal to start")
        if self.host_start is not None:
            host_end = self.host_start + (self.end - self.start)
            if host_end > 65535:
                raise ValueError("wireguard host port range exceeds 65535")
        return self

    @field_validator("host_ip")
    @classmethod
    def validate_host_ip(cls, value: str | None) -> str | None:
        if value is not None:
            ip_address(value)
        return value

    @property
    def effective_host_start(self) -> int:
        return self.host_start or self.start

    @property
    def effective_host_end(self) -> int:
        return self.effective_host_start + (self.end - self.start)

    def contains(self, port: int) -> bool:
        return self.start <= port <= self.end


class RpkiSpec(StrictModel):
    """RPKI cache 服务参数。

    Attributes:
        enabled: 是否启用 RPKI cache。
        cache_url: 上游 RPKI JSON 源地址。
        listen_host: 在 underlay 中绑定的监听地址。
        listen_port: 监听端口。
    """

    enabled: bool = True
    cache_url: str = "https://dn42.burble.com/roa/dn42_roa_46.json"
    listen_host: str = "10.254.42.3"
    listen_port: int = Field(default=8282, ge=1, le=65535)

    @field_validator("listen_host")
    @classmethod
    def validate_listen_host(cls, value: str) -> str:
        ip_address(value)
        return value


class RouterRuntimeSpec(StrictModel):
    """单节点 runtime 视图。

    它描述当前节点如何被部署成一组 Docker 容器（经 Docker Engine API），包括：

    - underlay 网络
    - router Dockerfile 参数
    - RPKI 配置
    - service 列表与它们之间的依赖关系

    Attributes:
        project_name: 运行时项目名；为 `None` 时通常由节点名自动推导。
        underlay: 节点 deployment 使用的 underlay 网络定义。
        rpki: RPKI cache 参数。
        router_dockerfile: 路由器镜像构建模板参数。
        services: 当前节点的 runtime 服务定义列表。
    """

    project_name: str | None = None
    underlay: UnderlayNetworkSpec
    rpki: RpkiSpec = Field(default_factory=RpkiSpec)
    router_dockerfile: RouterDockerfileSpec = Field(default_factory=RouterDockerfileSpec)
    wireguard_port_range: WireGuardPortRangeSpec | None = None
    services: list[RuntimeServiceSpec]

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized):
            raise ValueError("project_name must contain only lowercase letters, digits, underscores, and hyphens")
        return normalized

    @model_validator(mode="after")
    def validate_services(self) -> "RouterRuntimeSpec":
        names = [service.name for service in self.services]
        if len(names) != len(set(names)):
            raise ValueError("runtime service names must be unique")

        enabled_services = [service for service in self.services if service.enabled]
        roles = [service.role for service in enabled_services]
        required = {
            ServiceRole.ROUTER_NETNS,
            ServiceRole.WG_GATEWAY,
            ServiceRole.BIRD_ROUTER,
        }
        missing = sorted(role.value for role in required.difference(roles))
        if missing:
            raise ValueError(f"missing required runtime service roles: {', '.join(missing)}")

        known = {service.name for service in enabled_services}
        missing_dependencies = sorted(
            dependency
            for service in enabled_services
            for dependency in service.depends_on
            if dependency not in known
        )
        if missing_dependencies:
            raise ValueError(
                f"runtime services depend on unknown services: {', '.join(missing_dependencies)}"
            )

        missing_network_services = sorted(
            target
            for target in (_network_mode_service(service.network_mode) for service in enabled_services)
            if target and target not in known
        )
        if missing_network_services:
            raise ValueError(
                "runtime services use unknown network_mode services: "
                + ", ".join(missing_network_services)
            )

        network = ip_network(self.underlay.subnet, strict=False)
        gateway = ip_address(self.underlay.gateway)
        assigned: dict[str, str] = {}
        for service in enabled_services:
            if service.network_mode and service.ipv4_address:
                raise ValueError(
                    f"runtime service {service.name} must not set ipv4_address when using network_mode"
                )

            _validate_role_requirements(service)

            resolved = resolve_service_ipv4(self, service)
            if resolved is None:
                continue

            parsed = ip_address(resolved)
            if parsed not in network:
                raise ValueError(
                    f"runtime service {service.name} ipv4_address must belong to underlay subnet"
                )
            if parsed == gateway:
                raise ValueError(
                    f"runtime service {service.name} ipv4_address must not equal underlay gateway"
                )
            if resolved in assigned:
                raise ValueError(
                    f"runtime services {assigned[resolved]} and {service.name} share ipv4_address {resolved}"
                )
            assigned[resolved] = service.name

        _validate_service_ports(enabled_services)
        return self


def _network_mode_service(network_mode: str | None) -> str | None:
    if network_mode and network_mode.startswith("service:"):
        return network_mode.removeprefix("service:")
    return None


def _validate_role_requirements(service: RuntimeServiceSpec) -> None:
    volume_targets = {mount.target: mount for mount in service.volumes}

    if service.role in {ServiceRole.WG_GATEWAY, ServiceRole.BIRD_ROUTER} and not service.command:
        raise ValueError(f"runtime service {service.name} role {service.role.value} requires command")

    if service.role == ServiceRole.DNS and not service.network_mode:
        published = {(port.container_port, port.protocol) for port in service.ports}
        for protocol in ("udp", "tcp"):
            if (53, protocol) not in published:
                raise ValueError(
                    f"runtime service {service.name} role dns must publish container port 53/{protocol} "
                    "when it does not share another service's network namespace"
                )

    for target in _ROLE_REQUIRED_VOLUME_TARGETS.get(service.role, set()):
        if target not in volume_targets:
            raise ValueError(
                f"runtime service {service.name} role {service.role.value} requires volume target {target}"
            )

    for target in _ROLE_REQUIRED_WRITABLE_VOLUME_TARGETS.get(service.role, set()):
        mount = volume_targets.get(target)
        if mount is None:
            raise ValueError(
                f"runtime service {service.name} role {service.role.value} requires volume target {target}"
            )
        if mount.readonly:
            raise ValueError(
                f"runtime service {service.name} role {service.role.value} requires writable volume target {target}"
            )


def _validate_service_ports(services: list[RuntimeServiceSpec]) -> None:
    assigned: dict[tuple[str | None, int, str], str] = {}
    for service in services:
        if service.network_mode and service.ports:
            raise ValueError(
                f"runtime service {service.name} must not publish ports when using network_mode"
            )

        for port in service.ports:
            for host_port in _published_host_ports(port):
                key = (port.host_ip, host_port, port.protocol)
                if key in assigned:
                    raise ValueError(
                        f"runtime services {assigned[key]} and {service.name} share published port {render_port_publish(port)}"
                    )
                assigned[key] = service.name


def _port_publish_spec_from_string(value: str) -> dict[str, str | int | None]:
    protocol = "tcp"
    body = value.strip()
    if "/" in body:
        body, protocol = body.rsplit("/", 1)

    parts = body.split(":")
    if len(parts) == 1:
        start, end = _parse_port_range(parts[0])
        return {"container_port": start, "container_port_end": end, "protocol": protocol}
    if len(parts) == 2:
        host_start, host_end = _parse_port_range(parts[0])
        container_start, container_end = _parse_port_range(parts[1])
        return {
            "host_port": host_start,
            "host_port_end": host_end,
            "container_port": container_start,
            "container_port_end": container_end,
            "protocol": protocol,
        }
    if len(parts) == 3:
        host_start, host_end = _parse_port_range(parts[1])
        container_start, container_end = _parse_port_range(parts[2])
        return {
            "host_ip": parts[0],
            "host_port": host_start,
            "host_port_end": host_end,
            "container_port": container_start,
            "container_port_end": container_end,
            "protocol": protocol,
        }
    raise ValueError(f"invalid port publish string: {value}")


def _parse_port_range(value: str) -> tuple[int, int | None]:
    if "-" not in value:
        return int(value), None
    start, end = value.split("-", 1)
    return int(start), int(end)


def _published_host_ports(port: PortPublishSpec) -> range:
    if port.host_port is None:
        return range(0)
    return range(port.host_port, (port.host_port_end or port.host_port) + 1)


def render_port_publish(port: PortPublishSpec) -> str:
    """把结构化端口发布规则格式化成 `host:container/proto` 字符串表示。"""

    prefix = ""
    if port.host_port is not None:
        host = str(port.host_port)
        if port.host_port_end is not None and port.host_port_end != port.host_port:
            host = f"{port.host_port}-{port.host_port_end}"
        prefix = f"{host}:"
    if port.host_ip is not None:
        prefix = f"{port.host_ip}:{prefix}"
    container = str(port.container_port)
    if port.container_port_end is not None and port.container_port_end != port.container_port:
        container = f"{port.container_port}-{port.container_port_end}"
    return f"{prefix}{container}/{port.protocol}" if port.protocol != "tcp" else f"{prefix}{container}"


_ROLE_REQUIRED_VOLUME_TARGETS = {
    ServiceRole.WG_GATEWAY: {"/opt/dn42/scripts", "/etc/wireguard"},
    ServiceRole.BIRD_ROUTER: {"/opt/dn42/scripts", "/etc/bird"},
}


_ROLE_REQUIRED_WRITABLE_VOLUME_TARGETS: dict[ServiceRole, set[str]] = {}


_ROLE_DEFAULT_CAP_ADD: dict[ServiceRole, list[str]] = {
    ServiceRole.ROUTER_NETNS: ["NET_ADMIN", "NET_RAW"],
    ServiceRole.WG_GATEWAY: ["NET_ADMIN", "NET_RAW"],
    ServiceRole.BIRD_ROUTER: ["NET_ADMIN", "NET_RAW"],
    ServiceRole.DEBUG_SHELL: ["NET_ADMIN", "NET_RAW"],
}


_ROLE_DEFAULT_SYSCTLS: dict[ServiceRole, dict[str, str]] = {
    ServiceRole.ROUTER_NETNS: {
        "net.ipv4.conf.all.rp_filter": "0",
        "net.ipv4.conf.default.rp_filter": "0",
        "net.ipv4.ip_forward": "1",
        "net.ipv6.conf.all.forwarding": "1",
        "net.ipv6.conf.default.forwarding": "1",
    },
}


def role_default_cap_add(role: ServiceRole) -> list[str]:
    """返回某个角色推荐的默认 cap_add（未配置时回落使用）。"""

    return list(_ROLE_DEFAULT_CAP_ADD.get(role, []))


def role_default_sysctls(role: ServiceRole) -> dict[str, str]:
    """返回某个角色推荐的默认 sysctls（未配置时回落使用）。"""

    return dict(_ROLE_DEFAULT_SYSCTLS.get(role, {}))


def resolve_service_cap_add(service: RuntimeServiceSpec) -> list[str]:
    """解析服务最终的 cap_add：显式配置优先，否则回落到角色默认。"""

    return list(service.cap_add) if service.cap_add else role_default_cap_add(service.role)


def resolve_service_sysctls(service: RuntimeServiceSpec) -> dict[str, str]:
    """解析服务最终的 sysctls：显式配置优先，否则回落到角色默认。"""

    return dict(service.sysctls) if service.sysctls else role_default_sysctls(service.role)


def role_default_healthcheck(
    role: ServiceRole, runtime: RouterRuntimeSpec
) -> HealthCheckSpec | None:
    """返回某个角色推荐的默认健康检查（未配置时回落使用）。"""

    if role == ServiceRole.RPKI_CACHE and runtime.rpki.enabled:
        return HealthCheckSpec(
            test=["CMD-SHELL", f"nc -z 127.0.0.1 {runtime.rpki.listen_port}"],
            interval_seconds=30,
            timeout_seconds=5,
            retries=5,
            start_period_seconds=20,
        )
    return None


def resolve_service_healthcheck(
    runtime: RouterRuntimeSpec, service: RuntimeServiceSpec
) -> HealthCheckSpec | None:
    """解析服务最终的健康检查：显式配置优先，否则回落到角色默认。"""

    if service.healthcheck is not None:
        return service.healthcheck
    return role_default_healthcheck(service.role, runtime)


def resolve_service_ipv4(runtime: RouterRuntimeSpec, service: RuntimeServiceSpec) -> str | None:
    """解析某个 runtime service 在 underlay 中的 IPv4 地址。"""

    if service.ipv4_address is not None:
        return service.ipv4_address
    if service.role == ServiceRole.RPKI_CACHE and runtime.rpki.enabled:
        return runtime.rpki.listen_host
    if service.role == ServiceRole.ROUTER_NETNS:
        network = ip_network(runtime.underlay.subnet, strict=False)
        gateway = ip_address(runtime.underlay.gateway)
        candidate = ip_address(int(gateway) + 1)
        if candidate in network:
            return str(candidate)
    return None
