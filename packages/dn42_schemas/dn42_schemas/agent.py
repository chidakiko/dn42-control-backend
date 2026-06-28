from __future__ import annotations

"""控制面 ↔ 节点 Agent 之间的运行时协议模型。

三条主线：

* **Bootstrap**：`AgentRegistrationRequest` / `AgentRegistrationResponse`——
  `BootstrapStatus.ACCEPTED` 时 `node_id` / `agent_id` / `agent_token` /
  `desired_state_generation` 四者必须同时非空（`validate_accepted_response`）。
* **Apply 反馈**：`ApplyResult` 携带 `PlanSummary` 与错误列表，时间戳走
  ISO-8601 校验。
* **Reconcile 闭环**：`RuntimeSnapshot`（容器 / 接口 / WireGuard / BGP 观测）
  → `ReconciliationReport` + `DriftItem`。
“现状”表示该以 desired_generation 为准；`observed_generation` 不一定与它一致（漂移信号）。
详见 packages/docs/dn42_schemas.md。
"""

from dn42_common import (
    validate_ip_interface,
    validate_ip_network,
    validate_iso8601_timestamp,
    validate_wireguard_key,
)
from pydantic import Field, field_validator, model_validator

from .base import StrictModel
from .enums import (
    AgentCapability,
    ApplyStatus,
    BootstrapStatus,
    DriftSeverity,
    InterfaceKind,
    ObservationStatus,
    RuntimeResourceStatus,
    ServiceRole,
)

# generation 是控制面单调递增计数；上限取 PostgreSQL int32（21.4 亿），既远超任何真实
# 部署的世代数，又把 agent 误报/恶意的超大值挡在校验边界外——否则会在 PG int32 列上溢出
# （SQLite 动态宽度会悄悄存下、PG 报 NumericValueOutOfRange）。
_INT32_MAX = 2_147_483_647


class PlanSummary(StrictModel):
    """`FilePlan` 的聚合计数，同时也被 `ApplyResult` 复用作为变动概要。"""

    create: int = 0
    update: int = 0
    delete: int = 0
    noop: int = 0


class AppliedFileRecord(StrictModel):
    """一次 apply 中实际落盘的单个文件记录，供控制面审计。

    Attributes:
        action: 实际执行的动作，例如 `create` / `update` / `delete`。
        path: 相对渲染根目录的文件路径。
        sha256: 写入内容的 SHA-256；`delete` 动作为 `None`。
    """

    action: str
    path: str
    sha256: str | None = None


class ApplyResult(StrictModel):
    """Agent 上报一次 apply 的总体结果。

    “成功但伴随驱动器警告”表达为 `status=DEGRADED` + `errors`非空；`SKIPPED`
    专指控制面选择不下发（如 desired_generation 未变化）。
    """

    node_id: str
    generation: int = Field(ge=1, le=_INT32_MAX)
    status: ApplyStatus
    started_at: str
    finished_at: str | None = None
    plan_summary: PlanSummary = Field(default_factory=PlanSummary)
    applied_files: list[AppliedFileRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @field_validator("started_at")
    @classmethod
    def _validate_started_at(cls, value: str) -> str:
        return validate_iso8601_timestamp(value)

    @field_validator("finished_at")
    @classmethod
    def _validate_finished_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_iso8601_timestamp(value)


class HostInventory(StrictModel):
    """节点首次注册时上报的宿主机信息与所载能力。控制面据 `capabilities` 判断可下发资源集。"""

    hostname: str
    os: str
    arch: str
    kernel: str | None = None
    container_runtime: str | None = None
    container_runtime_version: str | None = None
    has_systemd: bool = False
    capabilities: list[AgentCapability] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


class AgentRegistrationRequest(StrictModel):
    """Agent 发起注册请求：携 `enrollment_token` + 目标 node_id + inventory。

    `requested_node_id` 必填：节点身份必须由部署方显式声明，控制面不做
    "猜测绑定"——避免一个配置不全的 agent 意外拿到别的节点的身份。
    """

    enrollment_token: str
    requested_node_id: str
    inventory: HostInventory


class AgentRegistrationResponse(StrictModel):
    """控制面返回的注册响应。

    `status == ACCEPTED` 时四个身份字段（`node_id` / `agent_id` /
    `agent_token` / `desired_state_generation`）必须同时非空；
    `validate_accepted_response` 负责在 schema 层占住这个不变量。
    """

    status: BootstrapStatus
    node_id: str | None = None
    agent_id: str | None = None
    agent_token: str | None = None
    desired_state_generation: int | None = Field(default=None, ge=1, le=_INT32_MAX)
    message: str | None = None

    @model_validator(mode="after")
    def validate_accepted_response(self) -> "AgentRegistrationResponse":
        if self.status == BootstrapStatus.ACCEPTED:
            missing = [
                name
                for name, value in {
                    "node_id": self.node_id,
                    "agent_id": self.agent_id,
                    "agent_token": self.agent_token,
                    "desired_state_generation": self.desired_state_generation,
                }.items()
                if value is None
            ]
            if missing:
                raise ValueError(f"accepted registration is missing: {', '.join(missing)}")
        return self


class WireGuardKeyReport(StrictModel):
    """节点上报其**唯一**的 WireGuard 公钥 + 托管密文，触发控制面一致性校验。

    一节点一把私钥，所有 peer 共用——故上报是节点级而非逐接口。

    Attributes:
        node_id: 上报节点。
        public_key: 节点 WG 公钥，由本地私钥推导——比对它即同时验证持有性，无需签名。
        private_key_escrow: 私钥经"恢复公钥"封装后的 base64 密文；控制面只存不解。
            恢复公钥未配置时为 ``None``（仅做一致性校验、不托管）。
    """

    node_id: str
    public_key: str
    private_key_escrow: str | None = None

    @field_validator("public_key")
    @classmethod
    def _validate_public_key(cls, value: str) -> str:
        return validate_wireguard_key(value)


class WireGuardKeyReportResult(StrictModel):
    """``POST /agent/wireguard-keys`` 的响应。

    ``status`` 取值：``stored``（首次登记）/ ``matched``（与记录一致）/
    ``rejected``（公钥与记录不符，409）。``propagated_to`` 是因本次登记而被回填
    对端公钥、重新物化的节点列表（内部 peering 双端打通）。
    """

    node_id: str
    accepted: bool
    status: str
    detail: str | None = None
    propagated_to: list[str] = Field(default_factory=list)


class RecoveryPublicKeyResponse(StrictModel):
    """``GET /agent/recovery-public-key``：分发离线托管恢复公钥（PEM）+ 指纹。

    公钥非秘密；其真实性由 TLS 保证，``fingerprint`` 供节点侧记录核对。
    ``configured=False`` 表示控制面未配置恢复公钥，节点跳过托管、仅上报公钥。
    """

    configured: bool
    public_key_pem: str | None = None
    fingerprint: str | None = None


class ObservedContainer(StrictModel):
    """Agent 观测到的受管容器状态快照。

    `config_hash` 是容器创建时贴上的内容寻址身份（`dn42.config_hash` label）；
    与期望哈希不一致即说明容器定义已漂移，需要 recreate。`None` 表示容器
    没有该 label（外部创建 / 旧产物）。
    """

    name: str
    role: ServiceRole | None = None
    image: str | None = None
    config_hash: str | None = None
    status: RuntimeResourceStatus = RuntimeResourceStatus.UNKNOWN
    healthy: bool | None = None


class ObservedInterface(StrictModel):
    """节点上某条接口的观测状态。`addresses` 逐项走 `validate_ip_interface`。"""

    name: str
    kind: InterfaceKind | None = None
    addresses: list[str] = Field(default_factory=list)
    mtu: int | None = Field(default=None, ge=576, le=9000)
    status: RuntimeResourceStatus = RuntimeResourceStatus.UNKNOWN

    @field_validator("addresses")
    @classmethod
    def validate_observed_addresses(cls, value: list[str]) -> list[str]:
        for address in value:
            validate_ip_interface(address)
        return value


class ObservedBgpProtocol(StrictModel):
    """从 BIRD `show protocols` 提取的单个 BGP protocol 状态。`session` 是在反查时填充的 schema session 名。"""

    name: str
    session: str | None = None
    state: str
    since: str | None = None
    info: str | None = None

    @field_validator("since")
    @classmethod
    def _validate_since(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_iso8601_timestamp(value)


class ObservedWireGuardPeer(StrictModel):
    """`wg show <iface> dump` 的单个 peer 观测——隧道活跃度的**原始事实**。

    只存 dump 直接给出的稳定字段；``up`` / ``stale`` / ``down`` 的判定刻意留给消费端
    （按 ``last_handshake_seconds`` 阈值，WG healthy 隧道约每 2 分钟握手一次），让前端
    能结合快照新鲜度再判，schema 不烘焙时钟相关结论。公钥不做强校验——观测字段的小
    瑕疵不该拖累整份快照被拒。

    Attributes:
        public_key: 对端公钥（定位 peer）。
        endpoint: 内核当前钉住的 ``ip:port``；``(none)`` / 未连为 None。
        last_handshake_seconds: 采集时刻距最近一次握手的秒数；从未握手为 None。
        transfer_rx_bytes / transfer_tx_bytes: 该 peer 累计收 / 发字节。
    """

    public_key: str
    endpoint: str | None = None
    last_handshake_seconds: int | None = Field(default=None, ge=0)
    transfer_rx_bytes: int = Field(default=0, ge=0)
    transfer_tx_bytes: int = Field(default=0, ge=0)


class ObservedWireGuardInterface(StrictModel):
    """`wg show` 类接口的观测结果：接口名 / 监听端口 / peer 数量 + per-peer 隧道状态。

    ``peers`` 为空兼容旧 agent（只采到 ``peer_count``）；新 agent 解析 dump 的 peer 行，
    填入每条隧道的握手 / 收发 / endpoint，供前端做隧道存活监控。
    """

    name: str
    listen_port: int | None = Field(default=None, ge=1, le=65535)
    peer_count: int = Field(default=0, ge=0)
    status: RuntimeResourceStatus = RuntimeResourceStatus.UNKNOWN
    peers: list[ObservedWireGuardPeer] = Field(default_factory=list)


class ObservedRoute(StrictModel):
    """从 BIRD ``show route all`` 提取的单条路由（用于路由表全表分析）。

    与 reconcile 的 drift 判定无关——这是独立于对账的路由观测，供控制面做
    Radar 式的全表统计（起源 AS、前缀长度、AS path、RPKI 分布）。字段都尽量
    取跨 BIRD 版本稳定的部分；解析不到的项留空，绝不臆造。

    Attributes:
        prefix: 路由前缀（CIDR）。
        origin_asn: AS path 最右端的起源 ASN；解析不到时为 ``None``。
        as_path: 完整 AS path（按出现顺序）；iBGP/直连路由可能为空。
        next_hop: 下一跳地址。
        protocol: 学到该路由的 BIRD protocol 名（≈ 来源 peer）。
        primary: 是否为 BIRD 选中的最优路由（``*`` 标记）。
        local: 是否为本节点本地起源（无 AS path 的 static / direct 路由）。这类
            路由不对外宣告，**不参与 RPKI**（``rpki`` 恒为 ``None``）、也不改写
            起源，仅打标签以便前端与外部学到的路由区分。
        communities: 标准 BGP community 列表，规范化为 ``"X:Y"`` 文本。
        large_communities: large community 列表，规范化为 ``"X:Y:Z"`` 文本
            （DN42 用它编码区域 / 延迟 / 策略等）。
        rpki: RPKI 校验结论 ``valid`` / ``invalid`` / ``not-found``（RFC6811 三态）；
            本地路由、未计算、或 ROA 表没采到时为 ``None``（不参与统计）。
    """

    prefix: str
    origin_asn: int | None = Field(default=None, ge=1, le=4294967295)
    as_path: list[int] = Field(default_factory=list)
    next_hop: str | None = None
    protocol: str | None = None
    primary: bool = True
    local: bool = False
    communities: list[str] = Field(default_factory=list)
    large_communities: list[str] = Field(default_factory=list)
    rpki: str | None = None

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        return validate_ip_network(value)


class AgentSelfMetrics(StrictModel):
    """Agent **进程自观测**：CPU/RSS + 背景循环耗时 + reconcile 累计。

    全部可选——旧 agent / 尚未采到时缺省 ``None``，控制面与前端据此降级显示。
    数据由 agent 周期写入本地 metrics.json（self-monitor 循环 + 各背景循环），构造
    ``RuntimeSnapshot`` 时一并带上，免去额外端点。时间戳为 best-effort 不做强校验，
    避免观测字段的小瑕疵拖累整份快照被拒。
    """

    cpu_percent: float | None = Field(default=None, ge=0)
    rss_mb: float | None = Field(default=None, ge=0)
    last_routing_collect_seconds: float | None = Field(default=None, ge=0)
    last_reresolve_seconds: float | None = Field(default=None, ge=0)
    last_reconcile_duration_seconds: float | None = Field(default=None, ge=0)
    total_reconciles: int | None = Field(default=None, ge=0)
    total_failures: int | None = Field(default=None, ge=0)
    consecutive_failures: int | None = Field(default=None, ge=0)
    self_observed_at: str | None = None
    last_reconcile_at: str | None = None


class RuntimeSnapshot(StrictModel):
    """节点 Agent 采集到的运行时快照，是 Reconcile 判定 drift 的唯一输入源。

    `generation` 表示 agent 当前已成功应用的 generation（来自本地 identity），
    不从容器 label 推导——容器身份是内容寻址的（config_hash），配置未变化时
    跨多代不重建，label 不携带 generation。
    """

    node_id: str
    generation: int | None = Field(default=None, ge=1, le=_INT32_MAX)
    captured_at: str
    containers: list[ObservedContainer] = Field(default_factory=list)
    interfaces: list[ObservedInterface] = Field(default_factory=list)
    wireguard_interfaces: list[ObservedWireGuardInterface] = Field(default_factory=list)
    bgp_protocols: list[ObservedBgpProtocol] = Field(default_factory=list)
    # 采集状态：区分"未采集 / 采集失败 / 已采集"，让对账不把采集失败当健康。
    # 默认 NOT_OBSERVED，使未显式采集这两维的快照（如纯容器视图）维持原跳过语义。
    wireguard_observation: ObservationStatus = ObservationStatus.NOT_OBSERVED
    bgp_observation: ObservationStatus = ObservationStatus.NOT_OBSERVED
    errors: list[str] = Field(default_factory=list)
    # Agent 进程自观测（CPU/RSS/背景循环耗时）。可选——旧 agent 不带，控制面随
    # last_snapshot JSON 自动透出给前端，无需新端点 / 迁移。
    self_metrics: AgentSelfMetrics | None = None

    @field_validator("captured_at")
    @classmethod
    def _validate_captured_at(cls, value: str) -> str:
        return validate_iso8601_timestamp(value)


class PrefilterRoute(StrictModel):
    """一条过滤前（import-table）路由的最小标识，用于列出无效 / 被拒路由。

    Attributes:
        prefix: 路由前缀（CIDR）。
        origin_asn: 起源 ASN（AS path 末位）；解析不到为 ``None``。
        protocol: 学到该路由的 BIRD protocol 名（≈ 来源 peer）。
    """

    prefix: str
    origin_asn: int | None = Field(default=None, ge=1, le=4294967295)
    protocol: str
    # 被拒首要原因（仅 filtered_routes 用；invalid_routes 恒为 None）：
    # ``out_of_range`` 前缀不在 DN42 合法范围 / 长度越界、``self_net`` 收到本节点自有网段、
    # ``as_path_too_long`` AS path > 8、``blocked_asn`` 命中拒收 ASN、``policy`` 其他策略兜底。
    reason: str | None = None


class PrefilterPeerStat(StrictModel):
    """单个 eBGP 对端**过滤前**（import-table / Adj-RIB-In）的 RPKI 统计。

    ``received`` 是对端发来、进 BIRD import-table 的条数（过滤前）；``accepted``
    是其中通过 import 过滤器、进主表的条数。两者之差 ≈ 被拒条数，而 ``invalid`` +
    ``not_found`` 揭示被拒里有多少是 RPKI 非法 / 未覆盖——主表（过滤后）看不到这些。

    Attributes:
        protocol: BIRD protocol 名（≈ 对端）。
        remote_asn: 对端 ASN（取 AS path 首位的众数）；解析不到为 ``None``。
        received: 过滤前收到条数（import-table）。
        accepted: 通过过滤进主表条数。
        valid / invalid / not_found: 对收到路由本地 RFC6811 校验的分布（三态）。
    """

    protocol: str
    remote_asn: int | None = Field(default=None, ge=1, le=4294967295)
    received: int = Field(default=0, ge=0)
    accepted: int = Field(default=0, ge=0)
    valid: int = Field(default=0, ge=0)
    invalid: int = Field(default=0, ge=0)
    not_found: int = Field(default=0, ge=0)


class PrefilterRpki(StrictModel):
    """节点级**过滤前** RPKI 分布（import-table 聚合）+ per-peer 明细。

    采集 BIRD 每个 eBGP channel 的 import-table（``import table;`` 保留的过滤前
    Adj-RIB-In），本地 RFC6811 校验得出"对端实际发来什么"。主表只剩 ROA_VALID，
    故只有这里才能看到 invalid / not-found 的真实规模与来源。
    """

    received: int = Field(default=0, ge=0)
    accepted: int = Field(default=0, ge=0)
    valid: int = Field(default=0, ge=0)
    invalid: int = Field(default=0, ge=0)
    not_found: int = Field(default=0, ge=0)
    peers: list[PrefilterPeerStat] = Field(default_factory=list)
    # 被过滤掉的 RPKI 无效路由明细（封顶，供前端列出"谁在发非法路由"）。
    invalid_routes: list[PrefilterRoute] = Field(default_factory=list)
    # 被本节点 import 过滤器主动拒绝、但**非** RPKI 无效的路由（bogon / 前缀长度 /
    # AS path / community 等策略原因）——过滤前收到却没进主表、又不在 invalid 里的。
    filtered_routes: list[PrefilterRoute] = Field(default_factory=list)


class RoutingTableSnapshot(StrictModel):
    """节点 BIRD 路由表的全量观测快照。

    这是**独立于 reconcile** 的周期性只读观测：agent 按自己的节奏（默认数分钟
    一次）采集 ``birdc show route all``，全量上报，控制面据此做聚合分析与时间
    序列。它绝不参与对账 / apply，不影响 ``applied_generation``。

    ``observation`` 沿用三态语义：``OBSERVED`` 时 ``routes`` 权威（空即真的空表）；
    ``UNAVAILABLE`` 表示采集失败（BIRD 不可达），控制面不应据此清空历史。
    """

    node_id: str
    captured_at: str
    observation: ObservationStatus = ObservationStatus.NOT_OBSERVED
    routes: list[ObservedRoute] = Field(default_factory=list)
    # 过滤前 RPKI 分布（import-table 聚合）。可选:旧 agent / 采集失败时为 None。
    prefilter: PrefilterRpki | None = None
    errors: list[str] = Field(default_factory=list)

    @field_validator("captured_at")
    @classmethod
    def _validate_captured_at(cls, value: str) -> str:
        return validate_iso8601_timestamp(value)


class WireGuardReresolveEntry(StrictModel):
    """一条被周期性重解析（re-set endpoint）的 WG 对端。

    背景：``wg syncconf`` 只在执行那一刻把 ``Endpoint`` 域名解析一次，内核随后
    钉死该 IP。对端走动态 DNS、IP 变更后我方不会自动刷新，隧道静默失联。Agent
    据 wireguard-tools 的 ``reresolve-dns`` 思路周期检查握手，超时即用配置里的
    域名重设 endpoint，让内核重新解析。本条记录一次这样的重设。

    Attributes:
        interface: WG 接口名。
        public_key: 对端公钥（定位 peer）。
        endpoint: 配置里的 endpoint（``host:port``，通常是域名）。
        previous_endpoint: 重设前内核里已钉死的 ``ip:port``（``(none)`` / 未知为 None）。
        resolved_endpoint: 重设后内核里的 ``ip:port``（取不到为 None）。
        stale_seconds: 距上次握手秒数；从未握手为 None。
    """

    interface: str
    public_key: str
    endpoint: str
    previous_endpoint: str | None = None
    resolved_endpoint: str | None = None
    stale_seconds: int | None = Field(default=None, ge=0)

    @field_validator("public_key")
    @classmethod
    def _validate_public_key(cls, value: str) -> str:
        return validate_wireguard_key(value)


class WireGuardReresolveReport(StrictModel):
    """Agent 周期 endpoint 重解析的结果——**独立于 reconcile** 的自愈观测。

    仅在本轮**确有重设**时上报（无 stale 对端时只在本地记日志、不打扰控制面）。
    ``checked`` 是本轮纳入检查的「域名 endpoint」对端总数，``reresolved`` 是其中
    被实际重设的。绝不参与对账 / apply，不影响 ``applied_generation``。
    """

    node_id: str
    captured_at: str
    checked: int = Field(default=0, ge=0)
    reresolved: list[WireGuardReresolveEntry] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @field_validator("captured_at")
    @classmethod
    def _validate_captured_at(cls, value: str) -> str:
        return validate_iso8601_timestamp(value)


class WireGuardTrafficSample(StrictModel):
    """节点 WG 流量的一次**轻量**采样——30s 高分辨率吞吐时间线的原始事实。

    与完整 ``RuntimeSnapshot``（容器 / 接口 / BGP 全采，节奏数分钟）刻意分开：agent
    用独立的轻量循环只跑一次 ``wg show all transfer``，把全部 peer 的累计收 / 发字节
    求和后上报，让控制面以 30s 粒度画吞吐曲线，而不必为高频流量去拉整份重快照。

    ``rx_bytes`` / ``tx_bytes`` 是**累计计数**（自接口建立起）；速率由控制面对相邻两次
    采样差分得出（计数器在接口重建时归零，差分钳到 ≥0）。绝不参与对账 / apply。

    Attributes:
        node_id: 上报节点。
        captured_at: 采样时刻（ISO-8601）。
        rx_bytes / tx_bytes: 该时刻全部 WG peer 的累计收 / 发字节之和。
        peer_count: 采样到的 peer 总数（仅供观测，不参与速率计算）。
    """

    node_id: str
    captured_at: str
    rx_bytes: int = Field(default=0, ge=0)
    tx_bytes: int = Field(default=0, ge=0)
    peer_count: int = Field(default=0, ge=0)

    @field_validator("captured_at")
    @classmethod
    def _validate_captured_at(cls, value: str) -> str:
        return validate_iso8601_timestamp(value)


class DriftItem(StrictModel):
    """控制面在 Reconcile 报告中报告的单项偏离。

    `desired` / `observed` 为可读字符串（如 `RUNNING` / `缺少卷 /etc/bird`），
    由上报者决定格式；schema 只保证类型。
    """

    component: str
    name: str
    severity: DriftSeverity
    message: str
    desired: str | None = None
    observed: str | None = None


class ReconciliationReport(StrictModel):
    """一次 Reconcile 的闭环报告。`observed_generation` 与 `desired_generation` 不一致即为全局漂移信号。"""

    node_id: str
    desired_generation: int = Field(ge=1, le=_INT32_MAX)
    observed_generation: int | None = Field(default=None, ge=1, le=_INT32_MAX)
    status: ApplyStatus
    captured_at: str
    drift: list[DriftItem] = Field(default_factory=list)

    @field_validator("captured_at")
    @classmethod
    def _validate_captured_at(cls, value: str) -> str:
        return validate_iso8601_timestamp(value)
