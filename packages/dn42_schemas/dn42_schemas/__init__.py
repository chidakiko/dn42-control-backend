from __future__ import annotations

"""dn42_schemas 的公开数据模型入口。

这个包定义了基础包之间共享的结构化协议对象，主要覆盖：

- 期望状态 `DesiredState`
- 网络与接口模型
- BIRD / BGP / IGP 路由模型
- runtime / 容器部署相关模型
- agent 观测与回报模型

调用方通常从这个模块直接导入公开类型，而不是深入子模块路径。
"""

from .agent import (
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    AgentSelfMetrics,
    AppliedFileRecord,
    ApplyResult,
    DriftItem,
    HostInventory,
    ObservedBgpProtocol,
    ObservedContainer,
    ObservedInterface,
    ObservedRoute,
    ObservedWireGuardInterface,
    PlanSummary,
    PrefilterPeerStat,
    PrefilterRoute,
    PrefilterRpki,
    RecoveryPublicKeyResponse,
    ReconciliationReport,
    RoutingTableSnapshot,
    RuntimeSnapshot,
    WireGuardKeyReport,
    WireGuardKeyReportResult,
    WireGuardReresolveEntry,
    WireGuardReresolveReport,
)
from .base import StrictModel
from .desired_state import DesiredState, TemplateSetSpec
from .dns import DnsForwardSpec, DnsRecordSpec, DnsSpec, DnsZoneSpec
from .enums import (
    AddressFamily,
    AgentCapability,
    ApplyStatus,
    BootstrapStatus,
    DriftSeverity,
    InterfaceKind,
    NodeHealth,
    ObservationStatus,
    RuntimeResourceStatus,
    ServiceRole,
)
from .network import InterfaceSpec, NodeSpec, WireGuardPeerSpec
from .io import load_desired_state
from .routing import (
    BfdSpec,
    BgpLargeCommunitySpec,
    BgpSessionSpec,
    Bird2ConfigSpec,
    BirdHostSpec,
    DummyInterfaceSpec,
    IgpAdjacencySpec,
    InternalTopologySpec,
)
from .runtime import (
    BuildSpec,
    HealthCheckSpec,
    PortPublishSpec,
    RouterDockerfileSpec,
    RouterRuntimeSpec,
    RpkiSpec,
    RuntimeServiceSpec,
    UnderlayNetworkSpec,
    VolumeMount,
    WireGuardPortRangeSpec,
    render_port_publish,
    resolve_service_cap_add,
    resolve_service_healthcheck,
    resolve_service_ipv4,
    resolve_service_sysctls,
    role_default_cap_add,
    role_default_healthcheck,
    role_default_sysctls,
)


__all__ = [
    "AddressFamily",
    "AgentCapability",
    "AgentRegistrationRequest",
    "AgentRegistrationResponse",
    "AgentSelfMetrics",
    "ApplyResult",
    "ApplyStatus",
    "AppliedFileRecord",
    "BfdSpec",
    "BgpLargeCommunitySpec",
    "BgpSessionSpec",
    "Bird2ConfigSpec",
    "BirdHostSpec",
    "BootstrapStatus",
    "BuildSpec",
    "DesiredState",
    "DriftItem",
    "DriftSeverity",
    "NodeHealth",
    "ObservationStatus",
    "DnsForwardSpec",
    "DnsRecordSpec",
    "DnsSpec",
    "DnsZoneSpec",
    "DummyInterfaceSpec",
    "HealthCheckSpec",
    "HostInventory",
    "IgpAdjacencySpec",
    "InterfaceKind",
    "InterfaceSpec",
    "InternalTopologySpec",
    "load_desired_state",
    "NodeSpec",
    "ObservedBgpProtocol",
    "ObservedContainer",
    "ObservedInterface",
    "ObservedRoute",
    "ObservedWireGuardInterface",
    "PlanSummary",
    "PortPublishSpec",
    "PrefilterPeerStat",
    "PrefilterRoute",
    "PrefilterRpki",
    "RecoveryPublicKeyResponse",
    "ReconciliationReport",
    "render_port_publish",
    "RouterRuntimeSpec",
    "RouterDockerfileSpec",
    "RoutingTableSnapshot",
    "RpkiSpec",
    "RuntimeResourceStatus",
    "RuntimeSnapshot",
    "RuntimeServiceSpec",
    "resolve_service_ipv4",
    "resolve_service_cap_add",
    "resolve_service_healthcheck",
    "resolve_service_sysctls",
    "role_default_cap_add",
    "role_default_healthcheck",
    "role_default_sysctls",
    "ServiceRole",
    "StrictModel",
    "TemplateSetSpec",
    "UnderlayNetworkSpec",
    "VolumeMount",
    "WireGuardKeyReport",
    "WireGuardKeyReportResult",
    "WireGuardPeerSpec",
    "WireGuardPortRangeSpec",
    "WireGuardReresolveEntry",
    "WireGuardReresolveReport",
]
