# 路线图与状态

本文是**前瞻性**待办：当前主线是「控制信息单一真相源清理」，外加若干 1.0 后增强项。已交付能力以代码与测试为准，原理见 [internals/architecture.md](internals/architecture.md)。

## 已交付（摘要）

- **控制面闭环**：整节点 provision、CRUD+materialize 单事务、materialize 行级锁串行化、generation 保留窗口/读取/diff/回滚、节点退役-收敛、peering 聚合根 + 一键化、健康五态强类型视图。
- **Agent 闭环**：source→render→observe→plan→execute→report；内容寻址 `dn42.config_hash` 最小扰动；tri-state 采集治"假绿"；Session 401 自愈；WG 私钥本地生成 + RSA-OAEP 托管 + 公钥 409 闸门；reconcile 指标落盘 + `--doctor`；声明式 underlay 重建；WG endpoint 周期重解析；route collector 喂送。
- **安全**：token 仅存 SHA-256、过期/轮换、注册审批闸门、Admin API fail-closed、append-only 审计日志。
- **可视化/运维**：SvelteKit 管理 UI + 一键互联向导、内部互联（iBGP/OSPF）合成视图与一致性不变量（见 [guides/monitoring-and-troubleshooting.md](guides/monitoring-and-troubleshooting.md#内部互联ibgp--ospf--internal_topology)）、共享 DNS 组 + anycast（见 [guides/dns-and-anycast.md](guides/dns-and-anycast.md)）。

---

## 主线：控制信息单一真相源清理（进行中）

### 北极星

控制面里存了很多"事实"。其中**副本**——同一事实的独立第二份，没有机制保证一致，renumber / 改密钥 / 改配置时"东改一处、漏改一处"且改不干净（历史事故见 [guides/monitoring-and-troubleshooting.md](guides/monitoring-and-troubleshooting.md#postmortempvg2-比同-as-节点少-1215-条路由2026-06-16) 的 pvg2 postmortem）。

**北极星：把"副本"逐个变成"派生"**（render/normalize/materialize 时从唯一真相源算出，不落独立存储）。概念分类与地址层面的完整盘点见 [reference/addressing-model.md](reference/addressing-model.md)。范例 `_normalize_dns_anycast`：`DnsGroup.bind_addresses` 单源 → 派生 `dns-anycast` 接口地址 + 任播前缀。

### 已完成（行为保持，golden 逐字节对账绿）

| 重复的事实 | 改法 |
| --- | --- |
| 节点 LLA `fe80::X` 各存一份在每条外部 eBGP 接口 addresses | `NodeSpec.link_local` 单源；materialize 派生注入外部 eBGP WG 接口；`backfill_node_lla.py` 剥副本 |
| 内部对端 `wireguard_peer.public_key` 存 spec 副本 | materialize 按 `peering.remote_node_id` 从对端 `Node.wireguard_public_key` 派生注入 |
| `base_template.node.*` 是 nodes 表列的陈旧副本 | provision 剥掉被 `_node_payload` 覆盖的身份字段 |
| 索引列 `name/kind/remote_asn/enabled` ↔ spec JSON 双写漂移 | `WgInterface/BgpSession.apply_spec` 从校验 spec 单源投影索引列 |
| `node_routing.routes` 死列（明细已在 `node_route_entries`） | 迁移 `f2a3b4c5d6e7` 删列 + 从模型移除 |

### 待定（需人参与，不宜盲改）

每条都触及**活跃 fleet 行为 / 存量数据 / agent 协议契约 / 寻址设计决策**，应配合单节点灰度或一次显式决策再做。

| 重复的事实 | 待做 | 为何需人参与 |
| --- | --- | --- |
| `internal_topology.hosts[*].ownip/ownip6` ← 各节点 `Node.loopback_*` | materialize + UI + 渲染三处都从 `Node.loopback_*` 派生 hosts；并加"某节点 loopback 变更时重物化所有引用它的节点"编排 | 多消费者 + 牵动全 fleet iBGP，须逐节点灰度验证收敛（不可逆风险） |
| `bgp_sessions[*].source_address` ← 绑定 `interface.addresses` | `source_address` 改可选 + 从绑定接口按地址族派生 + 强校验 | 牵动寻址模型决策（per-link /31 vs 节点单 /32+LLA，见 [reference/addressing-model.md](reference/addressing-model.md)），须先拍板 |
| `router_id` 与 `loopback_ipv4` 常恒等却分存 | 二选一为源，另一派生 | 破坏性 schema 变更，影响存量节点数据 |
| `NodeSpec.region` → `Bird2ConfigSpec.region` | 改名 `region_override` 使覆盖意图显式 | `StrictModel extra=forbid` 需 alias/迁移 |
| `BgpSessionSpec.neighbor` → `peer_routes` 镜像 | 加 validator：neighbor host 路由 ∈ peer_routes | 硬校验可能拒绝存量配置，须先全量核查 |
| `applied_generation` → `observed_generation` 术语混淆 | schema 注释/改名澄清 | 改 agent 上报协议字段 = 版本契约，需协调升级 |

推进方式：每条独立成 PR，配合单节点灰度 / 一次设计决策 / 存量迁移 / agent 协调升级；每消灭一个副本，更新 [reference/addressing-model.md](reference/addressing-model.md) 的副本清单。

---

## 1.0 后（其它增强）

| 事项 | 说明 |
| --- | --- |
| 多副本控制面 | EventBus 换 Redis Pub/Sub + 默认 Postgres，支持横向扩展（当前单进程 MVP，agent 周期 reconcile 兜底） |
| 批量 / fleet 重物化 | 模板升级后一键全节点重物化 |
| Admin RBAC / 多管理员 | 当前单 admin token |
| per-service toggle | 可选叶子服务（dns / debug-shell）的单独启用/禁用端点 |
| Agent 自身 healthz HTTP 探针 | 指标已落盘 + doctor 已就绪；如需被外部探活再补本地 HTTP 端点 |
| Agent token 形状强制 | 自动生成 token 形如 `<id>.<secret>` 与 base64url 字面量不同形，统一后在鉴权侧强制 |
| 更多 runtime backend | 纯 systemd 网络管理等 |
