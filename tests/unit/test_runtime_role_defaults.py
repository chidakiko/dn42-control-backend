from __future__ import annotations

"""per-ServiceRole 默认值与校验（P2-3）测试。"""

import pytest
from pydantic import ValidationError

from dn42_schemas import (
    BuildSpec,
    HealthCheckSpec,
    PortPublishSpec,
    RouterRuntimeSpec,
    RuntimeServiceSpec,
    ServiceRole,
    UnderlayNetworkSpec,
    VolumeMount,
    resolve_service_cap_add,
    resolve_service_healthcheck,
    resolve_service_sysctls,
    role_default_cap_add,
    role_default_healthcheck,
    role_default_sysctls,
)


def test_role_default_cap_add_covers_network_roles_and_debug_shell() -> None:
    for role in (
        ServiceRole.ROUTER_NETNS,
        ServiceRole.WG_GATEWAY,
        ServiceRole.BIRD_ROUTER,
        ServiceRole.DEBUG_SHELL,
    ):
        assert role_default_cap_add(role) == ["NET_ADMIN", "NET_RAW"]

    assert role_default_cap_add(ServiceRole.DNS) == []
    assert "net.ipv4.ip_forward" in role_default_sysctls(ServiceRole.ROUTER_NETNS)
    assert role_default_sysctls(ServiceRole.DNS) == {}


def test_resolve_service_cap_add_prefers_explicit_then_default() -> None:
    explicit = RuntimeServiceSpec(
        name="custom",
        role=ServiceRole.DEBUG_SHELL,
        image="busybox",
        cap_add=["SYS_PTRACE"],
    )
    assert resolve_service_cap_add(explicit) == ["SYS_PTRACE"]

    defaulted = RuntimeServiceSpec(
        name="debug",
        role=ServiceRole.DEBUG_SHELL,
        image="busybox",
    )
    assert resolve_service_cap_add(defaulted) == ["NET_ADMIN", "NET_RAW"]
    assert resolve_service_sysctls(defaulted) == {}


def _core_services() -> list[RuntimeServiceSpec]:
    return [
        RuntimeServiceSpec(
            name="dn42-router-netns",
            role=ServiceRole.ROUTER_NETNS,
            build=BuildSpec(target="netns"),
            command=["sleep", "infinity"],
        ),
        RuntimeServiceSpec(
            name="dn42-wg-gateway",
            role=ServiceRole.WG_GATEWAY,
            build=BuildSpec(target="wg-gateway"),
            command=["/opt/dn42/scripts/wg/start-wg-gateway.sh"],
            network_mode="service:dn42-router-netns",
            volumes=[
                VolumeMount(source="wireguard", target="/etc/wireguard"),
                VolumeMount(source="scripts", target="/opt/dn42/scripts"),
            ],
        ),
        RuntimeServiceSpec(
            name="dn42-bird-router",
            role=ServiceRole.BIRD_ROUTER,
            build=BuildSpec(target="bird-router"),
            command=["/opt/dn42/scripts/bird/start-bird-router.sh"],
            network_mode="service:dn42-router-netns",
            volumes=[
                VolumeMount(source="bird", target="/etc/bird"),
                VolumeMount(source="scripts", target="/opt/dn42/scripts"),
            ],
        ),
    ]


def _runtime(extra: list[RuntimeServiceSpec]) -> RouterRuntimeSpec:
    return RouterRuntimeSpec(
        underlay=UnderlayNetworkSpec(subnet="10.254.42.0/24", gateway="10.254.42.1"),
        services=[*_core_services(), *extra],
    )


def test_standalone_dns_service_must_publish_port_53() -> None:
    dns = RuntimeServiceSpec(
        name="dn42-dns",
        role=ServiceRole.DNS,
        image="coredns/coredns:1.12.1",
        ports=[PortPublishSpec(container_port=53, host_port=53, protocol="udp")],
    )
    with pytest.raises(ValidationError):
        _runtime([dns])


def test_standalone_dns_service_with_tcp_and_udp_53_is_valid() -> None:
    dns = RuntimeServiceSpec(
        name="dn42-dns",
        role=ServiceRole.DNS,
        image="coredns/coredns:1.12.1",
        ports=[
            PortPublishSpec(container_port=53, host_port=53, protocol="udp"),
            PortPublishSpec(container_port=53, host_port=53, protocol="tcp"),
        ],
    )
    runtime = _runtime([dns])
    assert any(service.role == ServiceRole.DNS for service in runtime.services)


def test_dns_service_sharing_netns_does_not_require_published_53() -> None:
    dns = RuntimeServiceSpec(
        name="dn42-dns",
        role=ServiceRole.DNS,
        image="coredns/coredns:1.12.1",
        network_mode="service:dn42-router-netns",
        volumes=[VolumeMount(source="coredns", target="/etc/coredns")],
    )
    runtime = _runtime([dns])
    assert any(service.role == ServiceRole.DNS for service in runtime.services)


def test_rpki_cache_gets_default_healthcheck_on_listen_port() -> None:
    runtime = _runtime(
        [
            RuntimeServiceSpec(
                name="dn42-rpki-cache",
                role=ServiceRole.RPKI_CACHE,
                image="rpki/stayrtr:latest",
            )
        ]
    )
    service = next(s for s in runtime.services if s.role == ServiceRole.RPKI_CACHE)

    resolved = resolve_service_healthcheck(runtime, service)
    assert resolved is not None
    assert resolved.test == ["CMD-SHELL", f"nc -z 127.0.0.1 {runtime.rpki.listen_port}"]

    assert role_default_healthcheck(ServiceRole.DNS, runtime) is None


def test_rpki_cache_explicit_healthcheck_wins() -> None:
    explicit = HealthCheckSpec(test=["CMD", "/healthcheck"])
    service = RuntimeServiceSpec(
        name="dn42-rpki-cache",
        role=ServiceRole.RPKI_CACHE,
        image="rpki/stayrtr:latest",
        healthcheck=explicit,
    )
    runtime = _runtime([service])
    resolved = resolve_service_healthcheck(runtime, service)
    assert resolved is explicit

