# 地址概念模型（addressing model）

本文把项目里**所有类型的地址**盘一遍：每个地址**是什么、有什么用、真相源在哪、影响半径多大**，并按对"单一真相源"最关键的维度给每个地址分类。这是**概念**参考；fleet 的**具体数值分配**（各节点 loopback、`/27` + `/29` 分区）见 [../guides/addressing-and-renumber.md](../guides/addressing-and-renumber.md)。

> 相关阅读：
> - [../guides/addressing-and-renumber.md](../guides/addressing-and-renumber.md)：fleet 实际地址分配 + renumber 操作步骤。
> - [../guides/monitoring-and-troubleshooting.md](../guides/monitoring-and-troubleshooting.md)：`internal_topology` 的 iBGP/OSPF 语义与"各节点 hosts 必须一致"不变量。
> - [desired-state.md](desired-state.md)：承载这些地址的 schema 顶层结构。

## 三种分类：源 / 派生 / 副本

每个地址都标两件事——**影响半径**和**真相源分类**。后者直接决定它是不是 renumber 的危险点：

| 分类 | 含义 | renumber 时 | 例子 |
|---|---|---|---|
| **源（source）** | 被人手 authored 的权威值，改这里才算改 | 改这一处 | `Node.loopback_ipv4`、`DnsGroup.bind_addresses`、`NodeSpec.link_local` |
| **派生（derived）** | 渲染 / normalize / materialize 时从源算出来，不落独立存储 | **不用动，自动跟随** | `OWNIP`、`dns-anycast` 接口地址、外部 eBGP 接口上的 LLA |
| **副本（copy）** | 源的**独立第二份**，没有机制保证一致 | **必须手动同步，漏一处就坏** | `internal_topology.hosts[*].ownip`、`bgp.source_address` |

**重构北极星：把"副本"逐个变成"派生"。** 代码里已有两个黄金范例：

1. `_normalize_dns_anycast`（[../../packages/dn42_schemas/dn42_schemas/desired_state.py](../../packages/dn42_schemas/dn42_schemas/desired_state.py)）：`dns.bind_addresses` 是 DNS 服务地址的**唯一真源**，`dns-anycast` dummy 接口地址、它进 BGP 的任播前缀全部在 normalize 阶段派生。
2. `_interface_payload` 里的 **LLA 派生**（[../../apps/control-server/app/services/materializer.py:214](../../apps/control-server/app/services/materializer.py)）：`NodeSpec.link_local` 是节点 LLA 的唯一真源，materialize 时派生到所有外部 eBGP WG 接口的 `addresses`。

所有"副本"都应朝这个形态收敛。重构进度见 [../ROADMAP.md](../ROADMAP.md)。

---

## 按影响半径分层

从**影响半径最大**（动一下牵动全 fleet / 注册表）到**最小**（节点内部、链路层）。

### 第 1 层：节点身份地址 —— 影响半径：全 fleet + dn42 注册表 + DNS

这层是**最贵**的地址：一处真值，但被复制 / 派生到很多地方。

| 地址 | 是什么 / 用途 | 真相源 | 分类 | 影响半径 |
|---|---|---|---|---|
| `router_id` | BIRD router id；iBGP/OSPF 身份；large-community `origin_node_id` 取它低 16 位 | `Node.router_id` | 源 | iBGP/OSPF 会话重建；community 编号变化 |
| `loopback_ipv4` / `loopback_ipv6` | 节点身份 loopback。渲染成 `OWNIP`/`OWNIPv6`：内核导出源地址 `krt_prefsrc`、iBGP next-hop、iBGP `source address`、router id 兜底。必须 ∈ `ipv4_prefixes`/`ipv6_prefixes` | `Node.loopback_ipv4/ipv6` | 源 | **全 fleet**（见 1.1 副本）+ DNS 正反向记录 + 注册表 |
| `ipv4_prefixes` / `ipv6_prefixes` | 节点自有、对外宣告的聚合前缀。派生 static reject 路由、`is_self_net` ipset、iBGP 宣告前缀 | `Node.ipv4_prefixes/ipv6_prefixes` | 源 | 节点对 DN42 起源什么（注册表 route 对象） |

**派生自本层（安全，自动跟随）**：`OWNIP`/`OWNIPv6`、`router id OWNIP`、`krt_prefsrc`、`origin_node_id`、`ownnets*_ipset`、iBGP 的 `source address`/`next hop`——全部在 [../../packages/dn42_templates/dn42_templates/bird2.py](../../packages/dn42_templates/dn42_templates/bird2.py) / 模板里从 loopback、prefixes 算出。

#### 1.1 危险副本：`internal_topology.hosts[*].ownip / ownip6`

`bird.internal_topology.hosts[<node>].ownip/ownip6`（[BirdHostSpec](../../packages/dn42_schemas/dn42_schemas/routing.py)）是**每个节点 loopback 的独立第二份**，而且按 fleet 不变量要**复制进每一个节点**的 `base_template`。

- 影响半径：**全 fleet**。renumber 一个节点的 loopback，要同步 N 个节点里这份 hosts。
- 历史上"改不干净"最严重的点：见 [../guides/monitoring-and-troubleshooting.md](../guides/monitoring-and-troubleshooting.md) 的 pvg2 postmortem，以及 `deploy/unify_internal_topology.py` **硬编码旧地址会回退**的坑。
- 重构方向（**未完成**，见 [../ROADMAP.md](../ROADMAP.md)）：hosts 的 ownip/ownip6 应从各节点 `Node.loopback_*` 派生，而不是各自存一份。

### 第 2 层：eBGP 会话地址 —— 影响半径：单个对端

| 地址 | 是什么 / 用途 | 真相源 | 分类 | 影响半径 |
|---|---|---|---|---|
| `BgpSessionSpec.neighbor` | 对端（远端）BGP 地址；可带 `%zone` | 会话 spec | 源 | 一条会话 |
| `BgpSessionSpec.source_address` | 本端建会话的源地址 | 会话 spec | **副本** | 一条会话；**必须等于绑定接口上的某个地址** |

#### 2.1 危险副本：`source_address` vs 接口地址

`source_address` 与 `InterfaceSpec.addresses`（第 3 层）里的本端地址是**同一个值、独立写两遍**，没有任何东西强制相等。renumber 链路时改了接口漏了 source（或反之），BGP 就起不来。重构方向（**未完成**）：让 source 从绑定接口按地址族派生。

### 第 3 层：接口 / 链路地址 —— 影响半径：单条链路（两端）

承载在 `InterfaceSpec`（[network.py](../../packages/dn42_schemas/dn42_schemas/network.py)）/ `WgInterface.spec`。

| 地址 | 是什么 / 用途 | 真相源 | 分类 | 影响半径 |
|---|---|---|---|---|
| `addresses` | `ip addr add` 到接口的本端地址。eBGP wg：本端 `/31`(v4)+`/127`(v6)；外部 eBGP 接口的 `fe80::X/64` LLA 现**由 materialize 派生注入**（见下） | 接口 spec（LLA 部分为派生） | 源（LLA 派生） | 一条链路本端 |
| `peer_routes` | 经该接口可达的**对端**地址（`peer_v4/32`、`peer_v6/128`、对端 LLA） | 接口 spec | 副本（对端本地地址的镜像） | 一条链路；跨节点 |
| `wireguard_peer.endpoint` | 拨向对端的 underlay `host:port`（公网/NAT 地址，可为域名，agent 周期重解析） | 接口 spec | 源 | 一条链路 |
| `wireguard_peer.allowed_ips` | WG 加密选路范围，通常 `0.0.0.0/0` + `::/0` | 接口 spec | 源 | 一条链路 |

#### 3.1 已解决的副本：节点 LLA `fe80::X`（已收敛为派生）

节点本端 link-local（`fe80::X`，一节点一个，所有外部 eBGP WG 接口复用）**已从"副本"收敛为"派生"**：

- 唯一真源：`NodeSpec.link_local`（[network.py](../../packages/dn42_schemas/dn42_schemas/network.py)，裸 `fe80::/10` 地址，不带 `%zone`）。
- 派生：`materialize()` 的 `_interface_payload`（[materializer.py:214](../../apps/control-server/app/services/materializer.py)）对 `peering.is_internal=False` 的 WG 接口，把 `<link_local>/64` 注入接口 `addresses`（已存在则去重）。渲染器照旧与各接口 fe80 `peer_route` 配成 `ip addr add fe80::X/64 peer fe80::Y`，输出不变。
- 剥副本：存量节点接口 `addresses` 里的 `fe80::X/64` 副本由 `deploy` / `scripts/tools/backfill_node_lla.py` 一次性剥除，之后由派生重新提供（见 [../reference/cli-and-scripts.md](cli-and-scripts.md)）。
- 注意：**内部互联（iBGP/OSPF）WG 接口用各自的 LL**，不复用 `node.link_local`；对端 LL（`fe80::Y`）是 per-peer，留在 `peer_routes` 不动。

### 第 4 层：任播服务地址 —— 影响半径：跨节点共享 + 注册表

| 地址 | 是什么 / 用途 | 真相源 | 分类 | 影响半径 |
|---|---|---|---|---|
| `dns.bind_addresses` | DNS 任播服务地址。**唯一真源**：normalize 派生 `dns-anycast` dummy 接口（v4→`/32`、v6→`/128`）+ 登记 `track_service` ⇒ BIRD 起源任播前缀进 BGP | `DnsGroup.bind_addresses` | 源 | 订阅同组的**所有节点**（anycast）；注册表 `.56/29` 双 origin |
| `dns-anycast` 接口地址 + 任播前缀 | 上者的派生产物 | —（派生自 `bind_addresses`） | **派生（黄金范例）** | 同上 |
| DNS 记录值（A/AAAA/PTR 的 content） | 解析到 loopback / 任播地址 | `DnsRecord.content` | 副本（指向 loopback 的手工绑定） | 解析正确性；renumber 后必须跟改 |

`dns.bind_addresses → dns-anycast` 这条链是**全项目最干净的单一真相源实现**。详见 [../guides/dns-and-anycast.md](../guides/dns-and-anycast.md)。

### 第 5 层：runtime / underlay（容器层）—— 影响半径：节点内部

仅在单节点容器编排内有意义，不进公网 `/26`、`/48` 身份/任播分区。

| 地址 | 是什么 / 用途 | 真相源 | 分类 | 影响半径 |
|---|---|---|---|---|
| `runtime.underlay.subnet` / `gateway` / `ipv6_subnet` | router netns 容器互联网（`10.254.42.0/24` 一类）。`ipv6_subnet` 纳入容器 `config_hash`（声明式重建） | `UnderlayNetworkSpec` | 源 | 节点内部 |
| `runtime.rpki.listen_host` | underlay 内 RPKI cache 地址（默认 `10.254.42.3`）；渲染成模板 `rpki_ip` | `RpkiSpec.listen_host` | 源 | 节点内部 |
| `RuntimeServiceSpec.ipv4_address` | 某服务在 underlay 的显式地址；必须 ∈ underlay 子网且 ≠ 网关 | service spec | 源 | 节点内部 |

### 不随节点变的字面量

`is_valid_network` / `is_valid_network_v6` 里的 DN42 可接受前缀范围、anycast `172.2x.0.0/24` 段等，是**协议级常量**，不随本节点 renumber 变化，不属于"源/派生/副本"。

---

## 副本清单（renumber 危险点速查）

按重构优先级排（✅ = 已收敛为派生，⬜ = 仍是副本）。完整进度见 [../ROADMAP.md](../ROADMAP.md)。

| # | 副本 | 应派生自 | 状态 |
|---|---|---|---|
| 1 | `internal_topology.hosts[*].ownip/ownip6` | 各节点 `Node.loopback_*` | ⬜ 影响半径全 fleet，历史事故最多 |
| 2 | `bgp_sessions[*].source_address` | 绑定 `interface.addresses` | ⬜ 牵动寻址模型决策 |
| 3 | `interfaces[*].peer_routes` 对端地址 | 对端 `addresses`（peering 聚合根作单源） | ⬜ |
| 4 | DNS A/AAAA/PTR 记录 | 指向 loopback 的手工绑定 | ⬜ renumber 必须连带改 |
| 5 | 节点 LLA `fe80::X` | `NodeSpec.link_local` | ✅ **已派生**（materialize 注入 + backfill 剥副本） |
| — | 内部对端 WG 公钥 | 对端 `Node.wireguard_public_key` | ✅ **已派生**（materialize 注入） |
| — | `dns-anycast` 接口地址 / 任播前缀 | `DnsGroup.bind_addresses` | ✅ **已派生**（黄金范例） |

每消灭一个副本，就少一处"改不干净"。范例形态见 `_normalize_dns_anycast` 与 `_interface_payload`。
