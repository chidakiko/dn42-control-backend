from __future__ import annotations

"""跨包共用的枚举。

全部继承 `str, Enum`，以便直接序列化为 JSON / YAML 可读字符串；
也使 Pydantic 校验器在 `extra="forbid"` 下能接受原始字符串输入。
详细含义参见 packages/docs/dn42_schemas.md。
"""

from enum import Enum


class InterfaceKind(str, Enum):
    """`InterfaceSpec.kind`：接口的逻辑类型。决定渲染路径与校验规则。"""

    DUMMY = "dummy"
    WIREGUARD = "wireguard"
    UNDERLAY = "underlay"


class ServiceRole(str, Enum):
    """`RuntimeServiceSpec.role`：服务的语义角色。

    决定 `validate_services` 里的必需挂载、`resolve_service_ipv4` 的默认
    IP 推导、以及 looking-glass 身车服务的自动注入逻辑。
    """

    ROUTER_NETNS = "router-netns"
    WG_GATEWAY = "wg-gateway"
    BIRD_ROUTER = "bird-router"
    RPKI_CACHE = "rpki-cache"
    DNS = "dns"
    LOOKING_GLASS_PROXY = "looking-glass-proxy"
    LOOKING_GLASS_FRONTEND = "looking-glass-frontend"
    DEBUG_SHELL = "debug-shell"


class AddressFamily(str, Enum):
    """`BgpSessionSpec.address_family`：会话承载的地址族。“mp-bgp” 表示同会话务同时走 v4/v6。"""

    IPV4 = "ipv4"
    IPV6 = "ipv6"
    MP_BGP = "mp-bgp"


class ApplyStatus(str, Enum):
    """Agent 上报的 apply / reconcile 总体结果。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


class BootstrapStatus(str, Enum):
    """Agent 注册响应状态。仅 ACCEPTED 会携带 token / generation。"""

    ACCEPTED = "accepted"
    PENDING_APPROVAL = "pending-approval"
    REJECTED = "rejected"


class AgentCapability(str, Enum):
    """宿主机上 Agent 能加载的能力标签。控制面据此判断是否可以下发对应资源。"""

    DOCKER = "docker"
    SYSTEMD = "systemd"
    WIREGUARD = "wireguard"
    BIRD = "bird"
    COREDNS = "coredns"
    RPKI = "rpki"


class RuntimeResourceStatus(str, Enum):
    """观测到的 runtime 资源状态。`UNKNOWN` 是“查不到 / 不能确认”的默认值，非错误。"""

    RUNNING = "running"
    STOPPED = "stopped"
    MISSING = "missing"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class DriftSeverity(str, Enum):
    """`DriftItem.severity`：控制面上报告警 / 告知 / 拒绝的阈值参考。"""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class NodeHealth(str, Enum):
    """控制面对单个节点的健康判定(由 agent 上报的 report/apply 派生)。

    - ``OK``:最近上报无 drift、世代一致;
    - ``DEGRADED``:report/apply 为 failed/degraded,或存在 drift;
    - ``STALE``:观测世代落后于期望,或短时间未上报(控制面视角"落后中");
    - ``DOWN``:长时间(超过 down 阈值)完全没有上报——控制面视角判定节点宕机/失联,
      无论上一次已知状态如何都覆盖为 ``DOWN``;
    - ``UNKNOWN``:从未上报过任何 payload。
    """

    OK = "ok"
    DEGRADED = "degraded"
    STALE = "stale"
    DOWN = "down"
    UNKNOWN = "unknown"


class ObservationStatus(str, Enum):
    """某个观测维度（WireGuard / BGP）的采集结果状态。

    区分三态，避免把"采集失败"静默当成"健康"：

    - ``NOT_OBSERVED``：没有对应观察器（全新节点 / 无该角色容器），不参与对账；
    - ``UNAVAILABLE``：观察器存在但容器内命令执行失败，状态未知——不能当健康，
      对账时按"无法确认"产出可见的告警；
    - ``OBSERVED``：命令成功，结果权威。即使为空也代表"真的没有"，期望存在
      而观测为空即判 drift。
    """

    NOT_OBSERVED = "not-observed"
    UNAVAILABLE = "unavailable"
    OBSERVED = "observed"