# 路线图与状态

本文是**前瞻性**待办：当前主线是「控制信息单一真相源清理」，外加若干 1.0 后增强项。
1.0 已交付能力以代码与测试为准，完整能力清单见 [architecture.md](architecture.md)。

## 已交付（摘要）

1.0（已冻结）+ 1.0 后已补齐的能力，按域速览（细节见 architecture.md / 各专题文档）：

- **控制面闭环**：整节点 provision、CRUD+materialize 单事务、materialize 行级锁串行化、
  generation 保留窗口/读取/diff/回滚、节点退役-收敛、peering 一键化、健康视图强类型。
- **Agent 闭环**：source→render→observe→plan→execute→report；内容寻址 `dn42.config_hash`
  最小扰动；tri-state 采集治「假绿」；Session 401 自愈；WG 私钥本地生成+RSA-OAEP 托管+公钥 409 闸门；
  reconcile 指标落盘 + `--doctor`。
- **安全**：token 仅存 SHA-256、过期/轮换、注册审批闸门、Admin API fail-closed、append-only 审计日志。
- **可视化/运维**：SvelteKit 管理 UI + 一键互联向导、内部互联（iBGP/OSPF）合成视图与一致性不变量
  （见 [internal-interconnect.md](internal-interconnect.md)）、共享 DNS 组 + anycast（见 [addressing.md](addressing.md)）。

---

## 主线：控制信息单一真相源清理（进行中）

### 背景与北极星

控制面里存了很多「事实」。其中一类是**副本**——同一事实的独立第二份，没有机制保证一致，
renumber / 改密钥 / 改配置时「东改一处、漏改一处」，且改不干净（历史事故见
[internal-interconnect.md](internal-interconnect.md) 的 pvg2 postmortem 与 loopback renumber 经历）。

**北极星：把「副本」逐个变成「派生」**（render/normalize 时从唯一真相源算出，不落独立存储）。
概念分类（源 / 派生 / 副本）与地址层面的完整盘点见 [addressing.md](addressing.md)。
代码里已有黄金范例 `_normalize_dns_anycast`（`packages/dn42_schemas/.../desired_state.py`）：
`DnsGroup.bind_addresses` 单源 → 派生 `dns-anycast` 接口地址 + 任播前缀。所有清理都朝这个形态收敛。

> 关键架构事实：materializer 对 `interfaces` / `bgp_sessions` 是**纯 passthrough**
> （[materializer.py](../apps/control-server/app/services/materializer.py) `_assemble_snapshot`，地址/密钥写死在 spec，无派生层）。
> 要做「派生」就在 materialize（或 schema `_normalize_*`）阶段第一次引入派生逻辑——这是本主线的统一着力点。

### 发现与待做（按优先级）

| # | 重复的事实 | 真相源 → 副本 | 待做修改 | 影响半径 | 优先级 |
| --- | --- | --- | --- | --- | --- |
| 1 | 节点 loopback | `Node.loopback_*` → `internal_topology.hosts[*].ownip/ownip6`（复制进**每个**节点 base_template） | materialize/上下文构造时从各节点 `Node.loopback_*` 派生 hosts；`BirdHostSpec.ownip/ownip6` 降为可选/移除存储 | **全 fleet** | P1 |
| 2 | 节点 WG 公钥 | `Node.wireguard_public_key` → 内部对端 `wireguard_peer.public_key`（急切回填存进 spec） | 改为 materialize 时按 `peering.remote_node_id` 派生注入，不再 eager 写 spec；外部 peer（remote_node_id 空）仍人工配 | 内部对端节点 | P1 |
| 3 | 路由明细（历史遗留） | `node_route_entries` 表 → `node_routing.routes` JSON 列（恒写 None） | 确认全 fleet 旧 blob 已被覆盖为 NULL 后，迁移**删列** | 单表（存储） | P1 |
| 4 | eBGP 本端源地址 | `InterfaceSpec.addresses` → `BgpSessionSpec.source_address`（独立写两遍，无约束保证相等） | `source_address` 改可选 + 从绑定 `interface` 按地址族派生；过渡期加「source ∈ 接口 addresses」强校验。即 dnpeers 重构 | 单会话/单链路 | P2 |
| 5 | 索引列 vs spec JSON | 列 `enabled/name/kind/remote_asn`（WgInterface/BgpSession）↔ 同字段又在 `spec` JSON 内 | 明确「列=索引派生自 spec」单向：写入只认 spec、列由 spec 投影；或物化统一以列覆盖（现 `_bgp_payload` 仅覆盖 `enabled`）。消除写入点双写漂移 | 所有接口/会话写入路径 | P2 |
| 6 | 节点身份字段 | `nodes` 列（node_id/asn/router_id/loopback/prefixes）→ `base_template.node.*`（被 `_node_payload` 无条件覆盖的陈旧副本） | provision 时像 `dns` 一样把这些键从 base_template 剥掉（置空/不存），base_template.node 只留 DB 无列字段（如 region） | 低（已被覆盖，但易误导） | P2 |
| 7 | 节点 IPv4 身份 | `router_id` 与 `loopback_ipv4` 常恒等却分存（OWNIP = loopback_ipv4 or router_id） | 二选一为真相源（建议 loopback_ipv4 主、router_id 派生/兜底），另一个收敛；明确必填策略 | 节点级 | P3 |
| 8 | region | `NodeSpec.region` → `Bird2ConfigSpec.region`（`_default_region` = bird.region or node.region） | `Bird2ConfigSpec.region` 改名 `region_override` 使「覆盖」意图显式；默认走节点级 | 模板渲染 | P3 |
| 9 | 对端地址 | `BgpSessionSpec.neighbor` → `InterfaceSpec.peer_routes` / `wireguard_peer.allowed_ips`（对端 host 路由是 neighbor 的镜像，无约束） | schema 加 validator：neighbor 的 /32·/128 host 路由必须 ∈ peer_routes（先约束、后考虑派生） | 单链路 | P3 |
| 10 | 已应用世代（协议术语） | `LocalAgentIdentity.applied_generation`（agent 本地权威）→ `RuntimeSnapshot.generation` → `ReconciliationReport.observed_generation` | 统一术语：`observed_generation` 实为「agent 已应用版本」非「观测值」，schema 注释/改名澄清；控制面不另存「节点上次已应用世代」冗余记录 | 协议语义（无逻辑改动） | P3 |

### 推进方式

- 每条独立成 PR，**先做无 renumber、零行为变化的项**（#1/#2/#6 派生化对外渲染输出不变，可用既有渲染快照测试回归对比）。
- #3 删列需一支 alembic 迁移 + 先验证存量已 NULL。
- #4（dnpeers 源地址派生）牵动寻址模型，单独评估：先「source 从接口派生 + 强校验」（零 renumber），
  节点级地址收敛与 LLA 传输属更大改动，另案。
- 每消灭一个副本，更新 [addressing.md](addressing.md) 的「副本清单」对应条目（删除或标记为已派生）。

---

## 1.0 后（其它增强）

| 事项 | 说明 |
| --- | --- |
| 多副本控制面 | EventBus 换 Redis Pub/Sub + 默认 Postgres，支持横向扩展（当前单进程 MVP，agent 周期 reconcile 兜底） |
| 批量 / fleet 重物化 | 模板升级后一键全节点重物化 |
| Admin RBAC / 多管理员 | 当前单 admin token |
| per-service toggle | 可选叶子服务（dns / debug-shell）的单独启用/禁用端点（核心角色 schema 强制，不可禁用） |
| Agent 自身 healthz HTTP 探针 | 指标已落盘 + doctor 已就绪；如需被外部探活再补一个本地 HTTP 端点 |
| Agent token 形状强制（鉴权边界） | 自动生成 token 形如 `<id>.<secret>`（含点号）与 base64url 字面量不同形，统一生产格式后再在 resolve/鉴权侧强制 |
| Worker 后台任务 | 当前已移除空骨架；如需 registry 同步 / ROA 同步 / 告警等周期任务再引入（generation 清理已并入 materialize，RPKI 走 stayrtr 容器自拉） |
| 更多 runtime backend | Docker Engine 原生 adapter、纯 systemd 网络管理等 |

## 文档
入口 `docs/README.md`，按「教程 / 运维 / 参考 / 内部原理」分层。地址概念总览见 [addressing.md](addressing.md)。
