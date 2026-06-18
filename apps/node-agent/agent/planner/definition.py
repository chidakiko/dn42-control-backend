from __future__ import annotations

"""容器定义（ContainerDefinition）：计划与执行的同源中间产物。

**哈希的输入不再是 schema 序列化,而是即将发给Docker Engine API 
的最终 payload**。

- planner 对 `payload` 的 canonical JSON 取哈希,决定 keep/recreate;
- executor 严格按同一份 `payload` 物化容器——"计划哈希的对象 = 执行
  创建的对象"由构造保证;
- schema 怎么重构都与重建解耦:只要解析后的最终值不变,哈希就不变;
- `dn42.config_hash` label 仍然存在,但只承担**存储**职责(身份随容器走,
  agent 无状态、重装不触发重建),不再编码任何协议形状。

payload 是纯 JSON 值(dict/list/str/int/bool/None),所有集合字段排序,
保证跨进程逐字节可复算。镜像配方以 `dockerfile_sha256` 进入 payload,
Dockerfile 内容本身随定义携带、由执行端经 `fileobj` 内存构建。
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dn42_common import canonical_sha256_hex, container_labels
from dn42_runtime import render_router_dockerfile
from dn42_schemas import (
    DesiredState,
    RuntimeServiceSpec,
    VolumeMount,
    resolve_service_cap_add,
    resolve_service_healthcheck,
    resolve_service_ipv4,
    resolve_service_sysctls,
)

from ..core.naming import node_project_name, service_container_name


@dataclass(frozen=True, slots=True)
class ContainerDefinition:
    """单个容器的完整执行定义。

    Attributes:
        node_id: 归属节点。
        service_name: runtime service 名。
        role: 服务角色字符串(label 用)。
        container_name: 最终容器名。
        network_name: underlay 网络名(executor 建网用)。
        payload: 即将发给 Engine API 的 canonical 参数集(哈希输入)。
        dockerfile: 本地构建服务的 Dockerfile 内容;非构建服务为 None。
    """

    node_id: str
    service_name: str
    role: str
    container_name: str
    network_name: str
    payload: dict[str, Any]
    dockerfile: str | None = None

    @property
    def config_hash(self) -> str:
        """payload 的 canonical JSON SHA-256 前 16 位:容器的内容寻址身份。"""

        return payload_hash(self.payload)

    @property
    def labels(self) -> dict[str, str]:
        """容器应携带的 4 个标准 label(config_hash 不进 payload,避免自引用)。"""

        return container_labels(self.node_id, self.role, self.config_hash)


def payload_hash(payload: dict[str, Any]) -> str:
    """canonical JSON(排序键、紧凑分隔)的 SHA-256 前 16 位。"""

    return canonical_sha256_hex(payload)[:16]


def diff_payload_keys(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """两份 payload 顶层差异键(排序),用于产出可解释的 recreate reason。"""

    keys = set(old) | set(new)
    return sorted(key for key in keys if old.get(key) != new.get(key))


def build_node_definitions(state: DesiredState, rendered_dir: Path) -> dict[str, ContainerDefinition]:
    """为节点全部 enabled 服务构建容器定义,按 service 名索引。"""

    project = node_project_name(state)
    network_name = f"{project}_{state.runtime.underlay.name}"
    return {
        service.name: _build_definition(state, service, project, network_name, rendered_dir)
        for service in state.runtime.services
        if service.enabled
    }


def _build_definition(
    state: DesiredState,
    service: RuntimeServiceSpec,
    project: str,
    network_name: str,
    rendered_dir: Path,
) -> ContainerDefinition:
    container_name = service_container_name(project, service.name)
    image, dockerfile = _image_section(state, service, project)
    payload: dict[str, Any] = {
        "name": container_name,
        "image": image,
        "command": list(service.command),
        "environment": dict(sorted(service.environment.items())),
        "binds": _bind_entries(rendered_dir, service.volumes),
        "ports": _port_entries(service),
        "network": _network_section(state, service, project, network_name, container_name),
        "cap_add": sorted(resolve_service_cap_add(service)),
        "devices": sorted(service.devices),
        "sysctls": dict(sorted(resolve_service_sysctls(service).items())),
        "healthcheck": _healthcheck_section(state, service),
        "restart_policy": "unless-stopped",
        "init": True,
    }
    return ContainerDefinition(
        node_id=state.node.node_id,
        service_name=service.name,
        role=service.role.value,
        container_name=container_name,
        network_name=network_name,
        payload=payload,
        dockerfile=dockerfile,
    )


def _image_section(
    state: DesiredState, service: RuntimeServiceSpec, project: str
) -> tuple[dict[str, Any], str | None]:
    if service.build is not None:
        dockerfile = render_router_dockerfile(state.runtime.router_dockerfile)
        return (
            {
                "build": {
                    "tag": f"{project}-{service.name}:latest",
                    "target": service.build.target,
                    "args": dict(sorted(service.build.args.items())),
                    "dockerfile_sha256": hashlib.sha256(dockerfile.encode("utf-8")).hexdigest(),
                }
            },
            dockerfile,
        )
    if service.image is None:
        raise ValueError(f"runtime service {service.name} is missing image/build configuration")
    return {"ref": service.image}, None


def _bind_entries(rendered_dir: Path, volumes: list[VolumeMount]) -> list[dict[str, str]]:
    entries = [
        {
            "source": resolve_volume_source(rendered_dir, mount),
            "target": mount.target,
            "mode": "ro" if mount.readonly else "rw",
        }
        for mount in volumes
    ]
    return sorted(entries, key=lambda item: (item["source"], item["target"]))


def resolve_volume_source(rendered_dir: Path, mount: VolumeMount) -> str:
    source = mount.source
    if source.startswith("./") or source.startswith(".\\"):
        return str((rendered_dir / source[2:]).resolve())
    source_path = Path(source)
    if source_path.is_absolute():
        return str(source_path)
    candidate = rendered_dir / source
    if candidate.exists():
        return str(candidate.resolve())
    if "/" in source or "\\" in source or source.startswith("."):
        return str((rendered_dir / source).resolve())
    return source


def _port_entries(service: RuntimeServiceSpec) -> list[dict[str, Any]]:
    """把端口发布规则展开成逐端口条目(含 range 展开与去重)。"""

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str | None, int | None, int, str]] = set()
    for port in service.ports:
        container_end = port.container_port_end or port.container_port
        host_end = port.host_port_end or port.host_port
        for offset, container_port in enumerate(range(port.container_port, container_end + 1)):
            host_port = port.host_port + offset if port.host_port is not None else None
            if host_port is not None and host_end is not None and host_port > host_end:
                host_port = None
            key = (port.host_ip, host_port, container_port, port.protocol)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "host_ip": port.host_ip,
                    "host_port": host_port,
                    "container_port": container_port,
                    "protocol": port.protocol,
                }
            )
    return sorted(
        entries,
        key=lambda item: (
            item["container_port"],
            item["protocol"],
            item["host_port"] or 0,
            item["host_ip"] or "",
        ),
    )


def _network_section(
    state: DesiredState,
    service: RuntimeServiceSpec,
    project: str,
    network_name: str,
    container_name: str,
) -> dict[str, Any]:
    if service.network_mode:
        if service.network_mode.startswith("service:"):
            target = service.network_mode.split(":", 1)[1]
            return {"mode": f"container:{service_container_name(project, target)}"}
        return {"mode": service.network_mode}
    # underlay 的 IPAM（subnet/gateway）必须进哈希：网络定义变化 → 网络要
    # 重建 → 所有挂载容器必须随之重建，否则旧容器钉死旧网络、新 IPAM 永远
    # 落不下去。`network_mode: service:X` 的服务经依赖传播间接覆盖。
    return {
        "attach": {
            "network": network_name,
            "subnet": state.runtime.underlay.subnet,
            "gateway": state.runtime.underlay.gateway,
            "ipv4_address": resolve_service_ipv4(state.runtime, service),
            "aliases": [service.name, container_name],
        }
    }


def _healthcheck_section(state: DesiredState, service: RuntimeServiceSpec) -> dict[str, Any] | None:
    healthcheck = resolve_service_healthcheck(state.runtime, service)
    if healthcheck is None:
        return None
    return healthcheck.model_dump(mode="json")


__all__ = [
    "ContainerDefinition",
    "build_node_definitions",
    "diff_payload_keys",
    "payload_hash",
]
