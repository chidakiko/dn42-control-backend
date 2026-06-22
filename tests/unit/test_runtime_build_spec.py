from __future__ import annotations

""":class:`RuntimeServiceSpec` 中“image / build”互斥与发布端口归一化、以及
``render_router_dockerfile`` 渲染器的单元测试。

``RuntimeServiceSpec`` 是 runtime 容器服务定义的 schema 表示，本文件
锁定以下几项：

* ``BuildSpec`` 只声明多阶段 ``target`` 与 ``args``（Dockerfile 由 agent
  内存生成，没有 context/dockerfile 字段）；``args`` 会原样保留
  （例如 ``DEBIAN_MIRROR``）。
* ``image`` 与 ``build`` 不允许同时设置，避免 “本地构建但拉东西” 这类歧义。
* ``render_router_dockerfile`` 会将 ``RouterDockerfileSpec`` 中的 base image
  和 debian 镜像源实际处理到渲染输出中（覆盖 ``FROM`` 与镜像源替换接口）。
* ports 只接收结构化 ``PortPublishSpec``（compose 风格字符串端口的归一化垫片已删，
  残留字符串显式报错）；``render_port_publish`` 可反转为 ``host:container/proto``。
"""

import pytest
from pydantic import ValidationError

from dn42_runtime import render_router_dockerfile
from dn42_schemas import (
    BuildSpec,
    PortPublishSpec,
    RouterDockerfileSpec,
    RuntimeServiceSpec,
    ServiceRole,
    render_port_publish,
)


def test_runtime_service_accepts_explicit_build_spec() -> None:
    service = RuntimeServiceSpec(
        name="dn42-router-netns",
        role=ServiceRole.ROUTER_NETNS,
        build=BuildSpec(target="netns"),
    )

    assert service.build is not None
    assert service.build == BuildSpec(target="netns")


def test_runtime_service_preserves_build_args() -> None:
    service = RuntimeServiceSpec(
        name="dn42-router-netns",
        role=ServiceRole.ROUTER_NETNS,
        build=BuildSpec(target="netns", args={"DEBIAN_MIRROR": "mirror.example.invalid"}),
    )

    assert service.build is not None
    assert service.build.args == {"DEBIAN_MIRROR": "mirror.example.invalid"}


def test_runtime_service_rejects_simultaneous_image_and_build() -> None:
    with pytest.raises(ValueError, match="cannot set both image and build"):
        RuntimeServiceSpec(
            name="dn42-router-netns",
            role=ServiceRole.ROUTER_NETNS,
            image="example.invalid/router:latest",
            build=BuildSpec(target="bird-router"),
        )


def test_render_router_dockerfile_uses_runtime_settings() -> None:
    rendered = render_router_dockerfile(
        RouterDockerfileSpec(
            base_image="debian:bookworm-slim",
            debian_mirror="mirror.example.invalid",
        )
    )

    assert "FROM debian:bookworm-slim AS debian-base" in rendered
    assert "mirror.example.invalid/debian-security" in rendered
    assert "mirror.example.invalid/debian|g" in rendered


def test_runtime_service_ports_are_structured_and_render_round_trips() -> None:
    """端口只接受结构化 PortPublishSpec（compose 风格字符串端口的归一化垫片已删）；
    render_port_publish 把它格式化回 ``host:container/proto``。"""

    service = RuntimeServiceSpec(
        name="dn42-router-netns",
        role=ServiceRole.ROUTER_NETNS,
        ports=[
            PortPublishSpec(host_ip="127.0.0.1", host_port=5000, container_port=5000),
            PortPublishSpec(host_port=32001, container_port=30001, protocol="udp"),
        ],
    )

    assert render_port_publish(service.ports[1]) == "32001:30001/udp"


def test_runtime_service_rejects_legacy_string_ports() -> None:
    """compose 风格字符串端口不再被静默归一化——必须显式报错。"""

    with pytest.raises(ValidationError):
        RuntimeServiceSpec(
            name="dn42-router-netns",
            role=ServiceRole.ROUTER_NETNS,
            ports=["32001:30001/udp"],  # pyright: ignore[reportArgumentType]
        )
