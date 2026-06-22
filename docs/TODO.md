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

### 已完成（行为保持，全程 654 测试 + golden 逐字节对账绿）

| # | 重复的事实 | 改法 | 提交 |
| --- | --- | --- | --- |
| 6 | `base_template.node.*` 身份字段是 nodes 表列的陈旧副本 | provision 剥掉被 `_node_payload` 覆盖的 7 个身份字段（留无权威覆盖的 region/site） | `refactor(provision)` |
| 5 | 索引列 `name/kind/remote_asn/enabled` ↔ spec JSON 双写会漂移 | 加 `WgInterface/BgpSession.apply_spec`，所有写入点经它从校验 spec 单源投影索引列 | `refactor(db): apply_spec` |
| 3 | `node_routing.routes` 死列（明细已在 `node_route_entries`） | 迁移 `f2a3b4c5d6e7` 删列（batch 兼容 SQLite/PG）+ 从模型移除 | `refactor(db): 删死列` |
| 2 | 内部对端 `wireguard_peer.public_key` 急切回填存 spec 副本 | materialize 按 `peering.remote_node_id` 从对端 `Node.wireguard_public_key` 派生注入；`_propagate_to_peers` 只重物化不再编辑 spec | `refactor(materialize): WG 公钥派生` |

### 待定（需人参与，不宜在自主轮里盲改）

每条都给了精确改法；之所以未自动完成，是因为它们触及**活跃 fleet 行为 / 存量数据 / agent 协议契约 / 寻址设计决策**，应配合单节点灰度验证或一次显式决策再做。

| # | 重复的事实 | 待做修改 | 为何需人参与 | 优先级 |
| --- | --- | --- | --- | --- |
| 1 | `Node.loopback_*` → `internal_topology.hosts[*].ownip/ownip6`（复制进每个节点 base_template） | materialize + UI `build_internal_topology_view` + 渲染三处都从 `Node.loopback_*` 派生 hosts；`BirdHostSpec.ownip/ownip6` 降可选；并加**新编排**：某节点 loopback 变更时重物化所有引用它的节点 | **多消费者** + 需新编排 + 牵动全 fleet iBGP，须逐节点灰度验证收敛（不可逆风险） | P1 |
| 4 | `InterfaceSpec.addresses` → `BgpSessionSpec.source_address`（独立写两遍） | `source_address` 改可选 + 从绑定 `interface` 按地址族派生 + 强校验 | 牵动**寻址模型决策**（per-link /31 vs 节点单 /32+LLA，见 [addressing.md](addressing.md)），须先拍板 | P2 |
| 7 | `router_id` 与 `loopback_ipv4` 常恒等却分存 | 二选一为源（建议 loopback_ipv4 主），另一派生；明确必填策略 | 改 `NodeSpec` 必填字段语义，破坏性 schema 变更，影响存量节点数据 | P3 |
| 8 | `NodeSpec.region` → `Bird2ConfigSpec.region` | 改名 `region_override` 使「覆盖」意图显式 | 改名动到存量 `base_template.bird.region` 键，`StrictModel extra=forbid` 需 alias/迁移；价值仅命名 | P3 |
| 9 | `BgpSessionSpec.neighbor` → `peer_routes` / `allowed_ips` 镜像 | 加 validator：neighbor 的 host 路由 ∈ peer_routes | 硬校验可能拒绝**存量 fleet 配置**，须先全量核查不违例 | P3 |
| 10 | `applied_generation` → `observed_generation`（协议术语混淆） | schema 注释/改名澄清「已应用版本」非「观测值」 | 改 agent 上报协议字段 = 控制面/agent **版本契约**，需协调升级避免 skew | P3 |

### 推进方式

- 已完成的 4 项都是「派生取代存储、对外渲染输出逐字节不变」，靠 654 测试 + golden 回归保证行为保持。
- 待定项每条独立成 PR，配合单节点灰度（#1）/ 一次设计决策（#4）/ 存量数据迁移（#7/#8）/ agent 协调升级（#10）再做。
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
