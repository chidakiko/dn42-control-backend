# DesiredState 字段参考

`DesiredState` 是「单节点应运行什么」的完整声明式输入，schema_version v1；本文是其字段单一事实源。

源码位于 `packages/dn42_schemas/dn42_schemas/`：`desired_state.py`（顶层 + 校验 + normalize 钩子）、`network.py`、`routing.py`、`dns.py`、`runtime.py`、`enums.py`，示例见 `testing.py`。

相关文档：DB 事实如何变成本对象见 [../reference/database.md](../reference/database.md)；materializer / 控制面如何组装并下发见 [../internals/control-server.md](../internals/control-server.md)；地址的源/派生/副本分类见 [../reference/addressing-model.md](../reference/addressing-model.md)。

---

## StrictModel 不变量

本包所有 schema 对象继承 `StrictModel`（`packages/dn42_schemas/dn42_schemas/base.py:16`）：

- **`extra="forbid"`**：拒绝任何未知字段——控制面与 Agent 之间任何隐式协议漂移都会在解析期直接报错。
- **`frozen=True`**：模型不可变。唯一受控豁口是 `DesiredState.validate_references` 里 normalize 钩子用 `object.__setattr__` 回写 `runtime` / `interfaces` / `bird`；其它任何地方都按不可变对象使用。
- **`canonical_json()`**：稳定可复算的 JSON（排序键、紧凑分隔符、`ensure_ascii=False`），使控制面与 Agent 在不同进程对同一对象算出一致字节序列；用于内容寻址。
- **`canonical_sha256()`**：上述 canonical JSON 的 SHA-256 十六进制摘要，用于哈希门控 / 内容寻址。

---

## DesiredState（顶层）

源码 `packages/dn42_schemas/dn42_schemas/desired_state.py:50`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `schema_version` | `Literal["v1"]` | 否（默认 `"v1"`） | schema 版本号；当前只接受 `v1`。 |
| `generation` | `int`（`ge=1`） | 是 | 期望状态世代号，通常由控制面递增。 |
| `node` | `NodeSpec` | 是 | 节点身份、ASN、前缀、loopback、link-local。 |
| `runtime` | `RouterRuntimeSpec` | 是 | runtime 部署：underlay、Dockerfile、RPKI、服务列表、WG 端口范围。 |
| `bird` | `Bird2ConfigSpec` | 否（默认空配置） | BIRD2 模板高层配置。 |
| `interfaces` | `list[InterfaceSpec]` | 否（默认 `[]`） | 节点应创建的接口（dummy loopback、WireGuard 等）。 |
| `bgp_sessions` | `list[BgpSessionSpec]` | 否（默认 `[]`） | 节点应建立的 BGP 会话。 |
| `dns` | `DnsSpec \| None` | 否（默认 `None`） | 本地 DNS 服务配置；`None` 表示不生成 DNS。 |
| `templates` | `TemplateSetSpec` | 否（默认值） | 渲染时选用的模板集版本。 |

`validate_references`（`model_validator(mode="after")`）在解析后跑跨字段校验并执行 normalize 钩子，见下文「校验规则」与「Normalize 钩子」。

---

## NodeSpec

源码 `packages/dn42_schemas/dn42_schemas/network.py:23`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `node_id` | `str` | 是 | 节点唯一标识；通常也是模板层 / runtime 层稳定节点名。 |
| `site` | `str` | 是 | 站点 / 机房标识。 |
| `region` | `Dn42OriginRegionCommunity` | 否（默认 `ASIA_EAST`） | DN42 区域枚举（来自 `dn42_common`）。 |
| `asn` | `int`（`ge=1`） | 是 | 节点 ASN。 |
| `router_id` | `str` | 是 | BIRD router id，通常是稳定 IPv4；走 `validate_ip_address`。 |
| `ipv4_prefixes` | `list[str]` | 否（默认 `[]`） | 自有可宣告 IPv4 前缀；逐项走 `validate_dn42_ipv4_network`。 |
| `ipv6_prefixes` | `list[str]` | 否（默认 `[]`） | 自有可宣告 IPv6 前缀；逐项走 `validate_dn42_ipv6_network`。 |
| `loopback_ipv4` | `str \| None` | 否 | 节点 loopback IPv4；设置则必须 ∈ `ipv4_prefixes` 之一，且走 `validate_ip_address`。 |
| `loopback_ipv6` | `str \| None` | 否 | 节点 loopback IPv6；设置则必须 ∈ `ipv6_prefixes` 之一。 |
| `link_local` | `str \| None` | 否 | 节点级 IPv6 link-local（`fe80::/10`，不带 `%zone`）；走 `validate_ipv6_link_local_address`。**单一真相源**：见下。 |

**`link_local` 单一真相源**：一节点一个本端 LLA，所有**外部 eBGP** WireGuard 接口复用（WG 建邻 + eBGP-over-link-local 源）。materializer 把 `<link_local>/64` 派生到这些接口的 `addresses`，存量接口侧不再各存一份（配套 backfill 剥离副本）；渲染器再与各接口 fe80 `peer_route` 配成 `peer` 形式。**不含内部互联**：iBGP/OSPF 的内部 WG 接口用各自 LL，不取本字段。地址源/派生/副本分类见 [../reference/addressing-model.md](../reference/addressing-model.md)。

---

## InterfaceSpec

源码 `packages/dn42_schemas/dn42_schemas/network.py:132`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | `str` | 是 | 接口名；校验 ≤ 15 字符（Linux 限制）。 |
| `kind` | `InterfaceKind` | 是 | 接口类型，枚举 `dummy` / `wireguard` / `underlay`。 |
| `mtu` | `int \| None`（`ge=576, le=9000`） | 否（默认 `1420`） | 接口 MTU；`None` 交由 runtime / 脚本默认。 |
| `addresses` | `list[str]` | 否（默认 `[]`） | 配置到接口的地址；逐项走 `validate_ip_interface`（须带前缀，如 `/32`）。 |
| `peer_routes` | `list[str]` | 否（默认 `[]`） | 对端直连 / 宿主路由；逐项走 `validate_ip_network`。 |
| `listen_port` | `int \| None`（`ge=1, le=65535`） | 否 | WireGuard 监听端口。 |
| `private_key_ref` | `str \| None` | 否 | WireGuard 私钥引用；`kind=wireguard` 时必填。 |
| `wireguard_peer` | `WireGuardPeerSpec \| None` | 否 | WireGuard 对端；`kind=wireguard` 时必填，非 wireguard 时必须为 `None`。 |

模型级校验（`validate_wireguard_fields`）：`wireguard` 接口必须同时有 `private_key_ref` 与 `wireguard_peer`；非 wireguard 接口不得设 `wireguard_peer`。

---

## WireGuardPeerSpec

源码 `packages/dn42_schemas/dn42_schemas/network.py:95`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `public_key` | `str` | 是 | 对端公钥；走 `validate_wireguard_key`。 |
| `preshared_key_ref` | `str \| None` | 否 | 预共享密钥引用；解析方式由运行环境决定。 |
| `endpoint` | `str \| None` | 否 | 对端端点（`host:port`）；走 `validate_wireguard_endpoint`。 |
| `allowed_ips` | `list[str]` | 否（默认 `[]`） | peer allowed IP；逐项走 `validate_ip_network`。 |
| `persistent_keepalive_seconds` | `int \| None`（`ge=1`） | 否 | 持久保活间隔（秒）。 |

---

## RouterRuntimeSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:312`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `project_name` | `str \| None` | 否 | runtime 项目名；`None` 时由节点名推导。校验须匹配 `[a-z0-9][a-z0-9_-]*`。 |
| `underlay` | `UnderlayNetworkSpec` | 是 | underlay 网络定义。 |
| `rpki` | `RpkiSpec` | 否（默认值） | RPKI cache 参数。 |
| `router_dockerfile` | `RouterDockerfileSpec` | 否（默认值） | 路由器镜像构建模板参数。 |
| `wireguard_port_range` | `WireGuardPortRangeSpec \| None` | 否 | WG UDP 端口范围；设置后驱动端口发布注入（见 normalize 钩子）。 |
| `services` | `list[RuntimeServiceSpec]` | 是 | runtime 服务列表。 |

模型级校验 `validate_services` 见「校验规则」。

---

## RuntimeServiceSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:159`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | `str` | 是 | 服务名；同一 deployment 内唯一。 |
| `role` | `ServiceRole` | 是 | 服务语义角色（见枚举）。 |
| `image` | `str \| None` | 否 | 直接使用的镜像名；与 `build` 互斥。 |
| `build` | `BuildSpec \| None` | 否 | 构建参数；与 `image` 互斥。 |
| `ipv4_address` | `str \| None` | 否 | underlay 中显式 IPv4；走 `ip_address`。用 `network_mode` 时不得设置。 |
| `command` | `list[str]` | 否（默认 `[]`） | 启动命令；`wg-gateway` / `bird-router` 角色必填。 |
| `environment` | `dict[str, str]` | 否（默认 `{}`） | 环境变量。 |
| `ports` | `list[PortPublishSpec]` | 否（默认 `[]`） | 发布端口；用 `network_mode` 时不得发布端口。 |
| `network_mode` | `str \| None` | 否 | 网络模式，如 `service:<name>`；`service:` 目标须存在。 |
| `cap_add` | `list[str]` | 否（默认 `[]`） | 额外 Linux capability（未设时按角色默认回落）。 |
| `devices` | `list[str]` | 否（默认 `[]`） | 透传设备。 |
| `sysctls` | `dict[str, str]` | 否（默认 `{}`） | 创建时应用的 sysctl（未设时按角色默认回落）。 |
| `volumes` | `list[VolumeMount]` | 否（默认 `[]`） | 挂载卷；部分角色有必需挂载目标。 |
| `depends_on` | `list[str]` | 否（默认 `[]`） | 启动依赖；目标须存在。 |
| `healthcheck` | `HealthCheckSpec \| None` | 否 | 健康检查（未设时部分角色有默认）。 |
| `enabled` | `bool` | 否（默认 `True`） | 是否参与最终渲染。 |

模型级校验：`image` 与 `build` 不可同设。角色相关必需项（`_validate_role_requirements`）：`wg-gateway`/`bird-router` 必须有 `command`；不共享 netns 的 `dns` 角色须发布 53/udp + 53/tcp；`wg-gateway` 必须挂 `/opt/dn42/scripts` + `/etc/wireguard`，`bird-router` 必须挂 `/opt/dn42/scripts` + `/etc/bird`。

---

## VolumeMount

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:35`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `source` | `str` | 是 | 宿主路径 / 命名卷 / 相对来源。 |
| `target` | `str` | 是 | 容器内挂载目标。 |
| `readonly` | `bool` | 否（默认 `True`） | 是否只读。 |

---

## HealthCheckSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:49`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `test` | `list[str]` | 是 | 健康检查命令数组。 |
| `interval_seconds` | `int`（`ge=1`） | 否（默认 `15`） | 检查间隔。 |
| `timeout_seconds` | `int`（`ge=1`） | 否（默认 `5`） | 单次超时。 |
| `retries` | `int`（`ge=1`） | 否（默认 `5`） | 连续失败次数阈值。 |
| `start_period_seconds` | `int`（`ge=0`） | 否（默认 `10`） | 启动宽限期。 |

---

## BuildSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:67`。Dockerfile 内容由 `runtime.router_dockerfile` 在 agent 内生成并经 Docker Engine API 内存构建——无构建上下文目录、无落盘 Dockerfile。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `target` | `str \| None` | 否 | 多阶段构建 target 名。 |
| `args` | `dict[str, str]` | 否（默认 `{}`） | build args。 |

---

## PortPublishSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:83`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `container_port` | `int`（`ge=1, le=65535`） | 是 | 容器端口。 |
| `container_port_end` | `int \| None`（`ge=1, le=65535`） | 否 | 容器端口范围末端；须 ≥ `container_port`。 |
| `host_port` | `int \| None`（`ge=1, le=65535`） | 否 | 宿主端口；`None` 仅声明容器端口。 |
| `host_port_end` | `int \| None`（`ge=1, le=65535`） | 否 | 宿主端口范围末端；须先有 `host_port`。 |
| `protocol` | `str` | 否（默认 `"tcp"`） | 协议，限 `tcp` / `udp` / `sctp`（归一化为小写）。 |
| `host_ip` | `str \| None` | 否 | 绑定宿主地址；走 `ip_address`。 |

模型级校验 `validate_ranges`：仅给 `host_port` + `container_port_end` 时自动补算 `host_port_end`；宿主与容器端口范围大小须一致；`host_port_end` 须 ≤ 65535 且 ≥ `host_port`。

---

## WireGuardPortRangeSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:253`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `start` | `int`（`ge=1, le=65535`） | 是 | 容器侧端口范围起。 |
| `end` | `int`（`ge=1, le=65535`） | 是 | 容器侧端口范围止；须 ≥ `start`。 |
| `host_start` | `int \| None`（`ge=1, le=65535`） | 否 | 宿主侧起始端口；推导出的宿主末端不得 > 65535。 |
| `host_ip` | `str \| None` | 否 | 绑定宿主地址；走 `ip_address`。 |

派生属性：`effective_host_start`（= `host_start or start`）、`effective_host_end`、`contains(port)`。

---

## UnderlayNetworkSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:213`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | `str` | 否（默认 `"router_underlay"`） | underlay 网络名。 |
| `subnet` | `str` | 是 | underlay IPv4 子网 CIDR；走 `ip_network(strict=False)`。 |
| `gateway` | `str` | 是 | underlay IPv4 网关。 |
| `ipv6_subnet` | `str \| None` | 否 | underlay IPv6 ULA 子网；设置后启用 IPv6（NAT66 出网）。 |
| `ipv6_gateway` | `str \| None` | 否 | underlay IPv6 网关；设 `ipv6_subnet` 时必填，走 `ip_address`。 |

模型级校验：`ipv6_subnet` 设了就必须有 `ipv6_gateway`。

---

## RpkiSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:290`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `enabled` | `bool` | 否（默认 `True`） | 是否启用 RPKI cache。 |
| `cache_url` | `str` | 否（默认 dn42 burble ROA JSON） | 上游 RPKI JSON 源。 |
| `listen_host` | `str` | 否（默认 `"10.254.42.3"`） | underlay 监听地址；走 `ip_address`。 |
| `listen_port` | `int`（`ge=1, le=65535`） | 否（默认 `8282`） | 监听端口。 |

---

## RouterDockerfileSpec

源码 `packages/dn42_schemas/dn42_schemas/runtime.py:147`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `base_image` | `str` | 否（默认 `"debian:13-slim"`） | 路由器镜像基础镜像。 |
| `debian_mirror` | `str` | 否（默认 `"deb.debian.org"`） | Debian 包源镜像。 |

---

## Bird2ConfigSpec

源码 `packages/dn42_schemas/dn42_schemas/routing.py:231`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `region` | `Dn42OriginRegionCommunity \| None` | 否 | 节点 DN42 区域；`None` 回退到节点级。 |
| `internal_topology` | `InternalTopologySpec \| None` | 否 | AS 内部拓扑；存在时驱动 OSPF / iBGP 生成。 |
| `large_communities` | `BgpLargeCommunitySpec` | 否（默认值） | large community 编码 / 策略参数。 |
| `dn42_ratelimit` | `int`（`ge=1`） | 否（默认 `15`） | BIRD 默认限速参数。 |
| `import_limit` | `int`（`ge=0`） | 否（默认 `8500`） | 每 eBGP 对端 per-channel 默认导入前缀上限；`0` = 不限制。 |
| `import_limit_action` | `ImportLimitAction` | 否（默认 `"block"`） | 超限动作，见下。 |
| `disable_ebgp` | `bool` | 否（默认 `False`） | 是否整体禁用 eBGP 邻居生成。 |
| `export_ownnets` | `bool` | 否（默认 `True`） | 是否默认对外宣告自有前缀。 |
| `dummy_interfaces` | `dict[str, DummyInterfaceSpec]` | 否（默认 `{}`） | BIRD 引用的 dummy 接口映射。 |
| `stub_ifnames` | `list[str]` | 否（默认 `[]`） | 按 stub 处理的接口名。 |
| `stub_ifnames_append` | `list[str]` | 否（默认 `[]`） | 追加到默认 stub 集的接口名。 |
| `static_routes4` | `list[str]` | 否（默认 `[]`） | IPv4 静态路由表达式；首段走 `validate_ip_network`。 |
| `static_routes6` | `list[str]` | 否（默认 `[]`） | IPv6 静态路由表达式；首段走 `validate_ip_network`。 |

`ImportLimitAction` = `Literal["block", "restart", "disable", "warn"]`（`routing.py:22`）：`block` 丢多余前缀但保会话（防灌表/震荡首选，不 flap）；`restart`/`disable` 拆/关会话；`warn` 只告警。

---

## BgpSessionSpec

源码 `packages/dn42_schemas/dn42_schemas/routing.py:39`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | `str` | 是 | 会话逻辑名；同节点内唯一。 |
| `remote_asn` | `int`（`ge=1`） | 是 | 对端 ASN。 |
| `neighbor` | `str` | 是 | 对端地址；可纯 IP 或 `IPv6%iface`（带 zone）。 |
| `source_address` | `str` | 是 | 本端源地址；走 `validate_ip_address`。 |
| `address_family` | `AddressFamily` | 是 | 地址族，枚举 `ipv4` / `ipv6` / `mp-bgp`。 |
| `interface` | `str \| None` | 否 | 邻居所属接口名；链路本地 IPv6 邻居常需。 |
| `policy` | `str` | 否（默认 `"dnpeers"`） | 模板策略名，如 `dnpeers` / `internal`。 |
| `import_mode` | `str` | 否（默认 `"filter"`） | 模板导入模式。 |
| `export_mode` | `str` | 否（默认 `"filter"`） | 模板导出模式。 |
| `protocol_suffix` | `str` | 否（默认 `""`） | 追加到生成 protocol 名的后缀。 |
| `extended_next_hop` | `bool` | 否（默认 `False`） | 是否启用 extended next hop。 |
| `bfd` | `BfdSpec \| None` | 否（默认新建 `BfdSpec`） | BFD 参数；`None` 表示不生成 BFD。 |
| `route_reflector_client` | `bool` | 否（默认 `False`） | 是否视对端为 RR client。 |
| `import_limit` | `int \| None`（`ge=1`） | 否 | 本会话导入上限覆盖；`None` 用节点级。 |
| `import_limit_action` | `ImportLimitAction \| None` | 否 | 超限动作覆盖；`None` 用节点级。 |
| `enabled` | `bool` | 否（默认 `True`） | 是否参与渲染。 |

模型级校验 `validate_neighbor`：拆出 zone 后地址走 `validate_ip_address`；若 `neighbor` 带 zone 且设了 `interface`，两者须一致；IPv6 link-local 邻居必须给 `%zone` 或显式 `interface`。方法 `is_internal(own_asn)`：`remote_asn == own_asn` 或 `policy == "internal"` 即视为内部会话。

---

## BfdSpec

源码 `packages/dn42_schemas/dn42_schemas/routing.py:25`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `enabled` | `bool` | 否（默认 `True`） | 是否启用 BFD。 |
| `interval_ms` | `int`（`ge=50`） | 否（默认 `1000`） | 控制报文间隔（毫秒）。 |
| `multiplier` | `int`（`ge=1`） | 否（默认 `5`） | 连续丢失多少报文判失活。 |

---

## InternalTopologySpec

源码 `packages/dn42_schemas/dn42_schemas/routing.py:154`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `routers` | `list[str]` | 是 | 参与内部路由域的路由器节点名。 |
| `private_nodes` | `list[str]` | 否（默认 `[]`） | 仅内部可见的私有节点。 |
| `hosts` | `dict[str, BirdHostSpec]` | 是 | 节点名 → `BirdHostSpec`，iBGP/OSPF 邻居信息主来源。 |
| `igp_adjacencies` | `list[IgpAdjacencySpec]` | 否（默认 `[]`） | 显式 IGP 邻接（非全连接场景）。 |
| `full_mesh_ibgp` | `bool` | 否（默认 `True`） | 是否在 `routers` 间建 full-mesh iBGP。 |
| `ospf_v2` | `bool` | 否（默认 `True`） | 是否生成 OSPFv2。 |
| `ospf_v3` | `bool` | 否（默认 `True`） | 是否生成 OSPFv3。 |

模型级校验 `validate_topology_hosts`：`routers`、`private_nodes`、每条 `igp_adjacencies.node` 都必须在 `hosts` 里有 hostvars，否则报缺失。

---

## BirdHostSpec

源码 `packages/dn42_schemas/dn42_schemas/routing.py:105`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `ownip` | `str` | 是 | 节点 loopback / iBGP 标识 IPv4；走 `validate_ip_address`。 |
| `ownip6` | `str` | 是 | 节点 loopback / iBGP 标识 IPv6；走 `validate_ip_address`。 |
| `ibgp_rr_upstreams` | `list[str]` | 否（默认 `[]`） | RR 拓扑下的上游 RR 节点名。 |

---

## IgpAdjacencySpec

源码 `packages/dn42_schemas/dn42_schemas/routing.py:125`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `node` | `str` | 是 | 对端节点名，须在 topology hostvars 中。 |
| `cost` | `int \| None`（`ge=1`） | 否 | IGP 开销；`None` 交模板默认。 |
| `interface` | `str \| None` | 否 | 显式承载接口名。 |
| `iface_type` | `str` | 否（默认 `"ptp"`） | IGP 接口类型。 |

---

## DummyInterfaceSpec

源码 `packages/dn42_schemas/dn42_schemas/routing.py:141`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `ifname` | `str` | 是 | 出现在模板中的接口名。 |
| `track_service` | `bool` | 否（默认 `False`） | `True` 表示承载任播/服务地址（进 direct protocol）；`False` 仅作 stub。 |

---

## BgpLargeCommunitySpec

源码 `packages/dn42_schemas/dn42_schemas/routing.py:201`。各整数字段范围 `0 ~ 4294967295`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `origin_node_type` | `int` | 否（默认 `100`） | 标记来源节点 ID 的社区类型编号。 |
| `origin_region_type` | `int` | 否（默认 `101`） | 标记来源区域的社区类型编号。 |
| `policy_type` | `int` | 否（默认 `102`） | 标记策略字段的社区类型编号。 |
| `origin_node_id` | `int \| None` | 否 | 显式来源节点 ID；未设由模板推导。 |
| `policy_local_pref` | `int` | 否（默认 `10`） | local-pref 语义策略值。 |
| `policy_deprep` | `int` | 否（默认 `20`） | de-prepend 语义策略值。 |
| `rejected_asns` | `list[int]` | 否（默认 `[]`） | 标记为拒绝来源的 ASN；逐项须为合法 ASN。 |

---

## DnsSpec

源码 `packages/dn42_schemas/dn42_schemas/dns.py:107`。`None` 等价不部署 DNS；`enabled=False` 则保留配置但不输出 Corefile。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `enabled` | `bool` | 否（默认 `True`） | 是否部署 DNS。 |
| `bind_addresses` | `list[str]` | 是 | DNS 任播 / 服务地址**唯一真源**；逐项走 `validate_ip_address`。 |
| `cache_ttl_seconds` | `int`（`ge=0`） | 否（默认 `300`） | 缓存 TTL。 |
| `zones` | `list[DnsZoneSpec]` | 否（默认 `[]`） | 本地接管 zone。 |
| `forwards` | `list[DnsForwardSpec]` | 否（默认 `[]`） | 转发规则。 |

`bind_addresses` 驱动 `_normalize_dns_anycast` 合成 `dns-anycast` 接口，见「Normalize 钩子」。

---

## DnsZoneSpec

源码 `packages/dn42_schemas/dn42_schemas/dns.py:44`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `zone` | `str` | 是 | zone 名；允许单 label，允许含 `/`（RFC 2317 反向委派），走 `validate_domain_name(require_multi_label=False, allow_slash=True)`。 |
| `records_ref` | `str` | 是 | 外部 record 集逻辑引用（如 `zone://example.dn42`）。 |
| `primary_ns` | `str \| None` | 否 | SOA 主 NS FQDN；有内联 `records` 时必填。 |
| `admin_email` | `str \| None` | 否 | SOA 管理邮箱 zone 写法；有内联 `records` 时必填。 |
| `soa_refresh` | `int`（`ge=0`） | 否（默认 `86400`） | SOA refresh（秒）。 |
| `soa_retry` | `int`（`ge=0`） | 否（默认 `7200`） | SOA retry（秒）。 |
| `soa_expire` | `int`（`ge=0`） | 否（默认 `3600000`） | SOA expire（秒）。 |
| `soa_minimum` | `int`（`ge=0`） | 否（默认 `3600`） | SOA minimum（秒）。 |
| `default_ttl` | `int`（`ge=0`） | 否（默认 `3600`） | zone `$TTL`，单条 record 未给 TTL 时用。 |
| `records` | `list[DnsRecordSpec]` | 否（默认 `[]`） | 内联记录；非空触发 zone 文件渲染。 |

模型级校验：有内联 `records` 时 `primary_ns` 与 `admin_email` 必须同时提供（构造 SOA）。

---

## DnsRecordSpec

源码 `packages/dn42_schemas/dn42_schemas/dns.py:20`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `name` | `str` | 是 | 记录名；相对 owner / `@`（apex）/ FQDN。 |
| `type` | `str` | 是 | 记录类型；限 `A/AAAA/CNAME/NS/PTR/TXT/MX/SRV/CAA`，归一化为大写。 |
| `value` | `str` | 是 | rdata；语义由调用方保证。 |
| `ttl` | `int \| None`（`ge=0`） | 否 | 单条 TTL；省略回落 `default_ttl`。 |

---

## DnsForwardSpec

源码 `packages/dn42_schemas/dn42_schemas/dns.py:88`。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `zone` | `str` | 是 | 转发 zone；允许单 label，走 `validate_domain_name(require_multi_label=False)`。 |
| `upstreams` | `list[str]` | 是 | 上游 resolver；逐项走 `validate_ip_address`。 |

---

## TemplateSetSpec

源码 `packages/dn42_schemas/dn42_schemas/desired_state.py:30`。决定模板层选用哪套模板目录。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `bird` | `str` | 否（默认 `"config-bird2/v1"`） | BIRD 模板集版本。 |
| `wireguard` | `str` | 否（默认 `"config-wireguard/v1"`） | WireGuard 模板集版本。 |
| `coredns` | `str \| None` | 否（默认 `"config-coredns/v1"`） | CoreDNS 模板集版本；`None` 表示不使用。 |
| `docker` | `str` | 否（默认 `"config-docker/v1"`） | Docker 构建产物模板集版本。 |
| `scripts` | `str` | 否（默认 `"config-scripts/v1"`） | 启动 / 应用脚本模板集版本。 |

---

## 枚举

源码 `packages/dn42_schemas/dn42_schemas/enums.py`，全部继承 `(str, Enum)`，可直接接受原始字符串输入并序列化为可读字符串。

| 枚举 | 取值 | 用途 |
| --- | --- | --- |
| `InterfaceKind` | `dummy` / `wireguard` / `underlay` | `InterfaceSpec.kind`。 |
| `ServiceRole` | `router-netns` / `wg-gateway` / `bird-router` / `rpki-cache` / `dns` / `debug-shell` | `RuntimeServiceSpec.role`；决定必需挂载与默认 IP 推导。 |
| `AddressFamily` | `ipv4` / `ipv6` / `mp-bgp` | `BgpSessionSpec.address_family`。 |

> 注：`enums.py` 还含 `ApplyStatus`、`BootstrapStatus`、`AgentCapability`、`RuntimeResourceStatus`、`DriftSeverity`、`NodeHealth`、`ObservationStatus` 等，但这些属于 agent 上报 / 控制面观测协议，不出现在 `DesiredState` 中。

---

## 校验规则

`DesiredState.validate_references`（`desired_state.py:81`）按顺序执行：

1. **接口名唯一**：`interfaces[*].name` 不得重复。
2. **BGP 会话名唯一**：`bgp_sessions[*].name` 不得重复。
3. **BGP 会话引用的接口须存在**：每个非空 `session.interface` 必须出现在 `interfaces` 名集合里。
4. **每 WG 接口单一外部远端 ASN**：对 `enabled` 且非内部（`is_internal(node.asn)` 为假）的会话，按 `interface` 聚合 `remote_asn`；同一接口出现多个外部 ASN 即报错。
5. **内部拓扑须包含本节点**：若 `bird.internal_topology` 存在，`node.node_id` 必须 ∈ `routers + private_nodes`。

随后调用嵌套模型自身的校验（解析期已生效），关键有：

- **NodeSpec**：`loopback_ipv4 ∈ ipv4_prefixes`、`loopback_ipv6 ∈ ipv6_prefixes`；前缀走 DN42 网络校验；`link_local` 须为合法 `fe80::/10`。
- **InterfaceSpec**：名 ≤ 15 字符；wireguard 接口须有 `private_key_ref` + `wireguard_peer`，非 wireguard 不得有 `wireguard_peer`。
- **BgpSessionSpec**：neighbor / zone / interface 一致性；IPv6 link-local 邻居须给 `%zone` 或 `interface`。
- **InternalTopologySpec**：`routers` / `private_nodes` / `igp_adjacencies` 引用的节点都须在 `hosts` 里。
- **RouterRuntimeSpec.validate_services**（`runtime.py:349`）：
  - 服务名唯一；
  - **enabled 服务必须覆盖三个必需角色**：`router-netns`、`wg-gateway`、`bird-router`；
  - `depends_on` 与 `network_mode service:<name>` 引用的服务都须存在；
  - 用 `network_mode` 的服务不得同时设 `ipv4_address`；
  - 各角色必需挂载（`wg-gateway` / `bird-router` 的脚本与配置目录；非共享 netns 的 `dns` 须发布 53/udp+53/tcp）；
  - **服务 IPv4 须落在 underlay subnet 内**（经 `resolve_service_ipv4` 解析后），不得等于 underlay 网关，且不得与其它服务重复；
  - 发布端口（含范围）在同一 `(host_ip, host_port, protocol)` 上不得冲突；用 `network_mode` 的服务不得发布端口。
- **UnderlayNetworkSpec**：`ipv6_subnet` 设了必须有 `ipv6_gateway`。
- **PortPublishSpec / WireGuardPortRangeSpec**：端口范围一致性与上界校验。
- **DnsZoneSpec**：内联 records 须有 `primary_ns` + `admin_email`。

---

## Normalize 钩子（派生/注入/剥离）

`validate_references` 在所有校验后按 **`wireguard_port_publish → bird_control_socket → dns_runtime → dns_anycast`** 顺序归一化，全部经 `object.__setattr__` 回写到 frozen 对象。各钩子幂等——序列化后的 desired 回灌 agent 再次校验不会翻倍。

### `_normalize_wireguard_port_publish_runtime`（`desired_state.py:126`）

- 收集所有 `kind=wireguard` 接口的 `listen_port`；同节点内 `listen_port` 必须唯一（重复报错）。
- 若设了 `runtime.wireguard_port_range`，所有 `listen_port` 必须落在该范围内（越界报错）。
- 据 `wireguard_port_range` 合成一条托管 `PortPublishSpec`（`host_ip`/`effective_host_start`-`effective_host_end` → `start`-`end`，`udp`），**注入到 `router-netns` 角色服务的 `ports`**（已存在同 key 则不重复）。未设端口范围 / 无 WG 接口时原样返回。

### `_normalize_bird_control_socket`（`desired_state.py:227`）

- **无条件注入** bird-router 的 `/run/bird` 可写挂载（`source=runtime/bird-run`、`target=/run/bird`、`readonly=False`）。这是 bird-router 一等不变量：宿主 agent 经此直连 `bird.ctl` 采集路由。
- 已有 `/run/bird` 挂载则只校验其可写（只读 → 报错）。无 bird-router 服务（极简/异常状态）则原样返回。常量见 `runtime.py:16`。

### `_normalize_dns_runtime`（`desired_state.py:278`）

「分配组即启用」的服务面。据 `dns` 决定框架托管的 CoreDNS（`dns` 角色）服务：

- **`dns` 为 `None` 或 `enabled=False`** ⇒ **剥掉**所有 `dns` 角色服务（desired 不含 → agent 拆除 CoreDNS）。
- **启用且尚无 `dns` 角色服务** ⇒ **注入** CoreDNS（`name=dn42-dns`、`image=coredns/coredns:1.12.1`、`command=["-conf", "/etc/coredns/Corefile"]`、`network_mode=service:<router-netns>`、挂 `coredns → /etc/coredns`、依赖 router-netns + 可选 wg-gateway）。无 router-netns 服务则跳过注入。已有 `dns` 服务则保留（去重）。常量见 `runtime.py:22`。

### `_normalize_dns_anycast`（`desired_state.py:351`）

「分配组即启用」的网络面，与上一钩子同构。`dns.bind_addresses` 是 DNS 服务地址唯一真源（地址源/派生分类见 [../reference/addressing-model.md](../reference/addressing-model.md)）：

- 先按保留名 `dns-anycast`（`runtime.py:32`）**剔除** `interfaces` 与 `bird.dummy_interfaces` 中的同名残留（单源识别 ⇒ 幂等）。
- **启用（`dns` 非空、`enabled`、有 `bind_addresses`）** ⇒ **派生重建**：合成一条 `dns-anycast` dummy 接口承载这些地址（裸 IP → v4 `/32`、v6 `/128`），并登记为 `track_service=True` 的 `DummyInterfaceSpec` ⇒ BIRD direct_anycast 起源对应前缀、任播地址进 BGP。多节点订阅同组拿相同地址 ⇒ anycast。
- **未启用 / 无 bind 地址** ⇒ 只剔除不重建：地址既不挂内核也不宣告，未提供 DNS 的节点不会黑洞任播流量。

---

## 完整示例

下例摘自 `packages/dn42_schemas/dn42_schemas/testing.py` 的 `build_hkg1_example_state()`（HKG1 黄金样本），为可读性按 JSON 缩略呈现并加注释。注意：DNS 任播地址写在 `dns.bind_addresses`，由 `_normalize_dns_anycast` 派生出 `dns-anycast` 接口与 dummy 登记——它们**不**手写在 `interfaces` / `bird.dummy_interfaces` 里。

```jsonc
{
  "schema_version": "v1",
  "generation": 1,
  "node": {
    "node_id": "edge1",
    "site": "hkg",
    "region": "asia-east",            // Dn42OriginRegionCommunity
    "asn": 4242420000,
    "router_id": "172.20.0.62",
    "ipv4_prefixes": ["172.20.0.0/26"],
    "ipv6_prefixes": ["fdce:1111:2222::/48"],
    "loopback_ipv4": "172.20.0.62",   // ∈ ipv4_prefixes
    "loopback_ipv6": "fdce:1111:2222:9500::1"
  },
  "runtime": {
    "underlay": { "subnet": "10.254.42.0/24", "gateway": "10.254.42.1" },
    "router_dockerfile": { "base_image": "debian:13-slim", "debian_mirror": "deb.debian.org" },
    "services": [
      { "name": "dn42-router-netns", "role": "router-netns",
        "build": { "target": "netns" }, "command": ["sleep", "infinity"],
        "cap_add": ["NET_ADMIN", "NET_RAW"], "devices": ["/dev/net/tun:/dev/net/tun"] },
      { "name": "dn42-wg-gateway", "role": "wg-gateway",
        "build": { "target": "wg-gateway" },
        "command": ["/opt/dn42/scripts/wg/start-wg-gateway.sh"],
        "network_mode": "service:dn42-router-netns",
        "volumes": [ { "source": "wireguard", "target": "/etc/wireguard" },
                     { "source": "scripts", "target": "/opt/dn42/scripts" } ],
        "depends_on": ["dn42-router-netns"] },
      { "name": "dn42-bird-router", "role": "bird-router",
        "build": { "target": "bird-router" },
        "command": ["/opt/dn42/scripts/bird/start-bird-router.sh"],
        "network_mode": "service:dn42-router-netns",
        "volumes": [ { "source": "bird", "target": "/etc/bird" },
                     { "source": "scripts", "target": "/opt/dn42/scripts" },
                     // /run/bird 可写挂载——也可省略，由 _normalize_bird_control_socket 注入
                     { "source": "runtime/bird-run", "target": "/run/bird", "readonly": false } ],
        "depends_on": ["dn42-router-netns", "dn42-wg-gateway", "dn42-rpki-cache"] },
      { "name": "dn42-rpki-cache", "role": "rpki-cache", "image": "rpki/stayrtr:latest",
        "command": ["-checktime=false", "-cache=https://dn42.burble.com/roa/dn42_roa_46.json"] }
    ]
  },
  "bird": {
    "region": "asia-east",
    "large_communities": { "origin_node_id": 62 },
    "internal_topology": {
      "routers": ["edge1", "edge2"],          // 必须含本节点 edge1
      "hosts": {
        "edge1": { "ownip": "172.20.0.62", "ownip6": "fdce:1111:2222:9500::1" },
        "edge2": { "ownip": "198.18.1.3",  "ownip6": "fdce:1111:2222:ff01::3" }
      },
      "igp_adjacencies": [ { "node": "edge2", "cost": 10 } ]
    }
  },
  "interfaces": [
    { "name": "dn42-lo", "kind": "dummy", "mtu": null,
      "addresses": ["172.20.0.62/32", "fdce:1111:2222:9500::1/128"] },
    { "name": "as4242420001", "kind": "wireguard",
      "private_key_ref": "secret://nodes/edge1/wireguard/as4242420001/private-key",
      "addresses": ["172.20.0.62/32", "fdce:1111:2222:9500::1/128"],
      "peer_routes": ["172.20.0.105/32", "fdce:1111:2222:dead::11/128"],
      "wireguard_peer": { "public_key": "+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=",
                          "allowed_ips": ["0.0.0.0/0", "::/0"] } }
    // ... igp-edge2 内部 WG 接口略
  ],
  "bgp_sessions": [
    { "name": "demopeer_4242420001_ex01_v4", "remote_asn": 4242420001,
      "neighbor": "172.20.0.105", "source_address": "172.20.0.62",
      "address_family": "ipv4", "interface": "as4242420001" }
    // ... _v6 会话略
  ],
  "dns": {
    "bind_addresses": ["172.20.0.20", "172.20.0.22",
                       "fdce:1111:2222::20", "fdce:1111:2222::22"],
    "zones": [ { "zone": "example.dn42", "records_ref": "zone://example.dn42" },
               { "zone": "0.20.172.in-addr.arpa", "records_ref": "zone://0.20.172" } ],
    "forwards": [ { "zone": "dn42", "upstreams": ["172.20.0.53"] } ]
  },
  "templates": { "bird": "config-bird2/v1", "wireguard": "config-wireguard/v1",
                 "coredns": "config-coredns/v1", "docker": "config-docker/v1",
                 "scripts": "config-scripts/v1" }
}
```

> 经 `validate_references` 后，因 `dns.enabled` 默认为真且有 `bind_addresses`：
> - `interfaces` 会多出一条 `dns-anycast` dummy 接口（地址 `172.20.0.20/32`、`...::20/128` 等）；
> - `bird.dummy_interfaces` 会多出 `dns-anycast: { track_service: true }`；
> - `runtime.services` 会多出 `dn42-dns`（CoreDNS）服务；
> - bird-router 的 `/run/bird` 可写挂载即使省略也会被补齐。
