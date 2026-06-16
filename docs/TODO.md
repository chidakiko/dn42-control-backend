# 路线图与状态

**1.0 已冻结**（版本 1.0.0）。本文记录 1.0 已交付的能力与 1.0 后可推进的事项。
已完成能力以代码和测试为准，架构说明见 [architecture.md](architecture.md)。

## 1.0 已交付

### 控制面闭环
| 事项 | 说明 |
| --- | --- |
| 整节点 provision | `POST /admin/provision` 幂等落库并发布新一代 |
| CRUD + materialize 单事务 | 业务变更与重物化同事务，失败整体回滚；提交后才广播 |
| materialize 并发串行化 | 节点行级 `FOR UPDATE` 锁 + `UNIQUE(node_id, generation)` 兜底 |
| generation 保留窗口 | materialize 随写裁剪，每节点保留最近 100 代，表不无界增长 |
| 节点退役收敛 | `Node.lifecycle` + `/decommission`（下发空对端、停止宣告路由）+ `/recommission`；DELETE 须先退役防孤儿 |
| peering 一键化 | `/nodes/{id}/peerings/provision` 同事务建 Peering+WgInterface+(可选)BgpSession |
| SQLite 外键强制 | 连接级 `PRAGMA foreign_keys=ON`，`ondelete` 子句生效 |
| 健康视图强类型 | `NodeHealth` 枚举 + `FleetHealth`/`NodeHealthDetail`/`NodeStatusEvents` response schema |
| `/healthz` 探活 | 探 DB 连通性（503 when down）；`/api/v1/healthz` 为 liveness |
| 关键路径日志 | 鉴权失败、enrollment 无效、节点 rejected、WG 公钥冲突均记日志（不记 token 值） |

### Agent 闭环
| 事项 | 说明 |
| --- | --- |
| reconcile 闭环 | source→render→observe→plan→execute→report；Plan 一等公民，执行层照单不二次决策 |
| 最小扰动容器身份 | `dn42.config_hash` 内容寻址：generation 递增不重建容器，无关变更不扰动 BGP |
| 本机收敛（Python 编排） | `birdc configure` / 按接口 WireGuard 同步与拆除；"全量拉起"在计划层枚举 loopback+逐接口（每步独立检查，不再是容器内 bash glob） |
| 假绿三连修复 | 收敛失败计入 `apply_status`；采集 tri-state（`ObservationStatus`）区分"采集失败/真无"；上报成功才推进 `applied_generation` |
| WG/BGP/容器观测 | `docker exec` 采集，protocol 名经 `bird_protocol_name` 反查；drift 对账 |
| Session 状态机 | 401 → 作废 token → 重注册自愈，token 轮换不砖化 agent |
| 优雅停机 | SIGTERM/SIGINT → stop_event，跑完当前 reconcile、释放连接池 |
| WG 私钥托管 | 节点本地生成 + RSA-OAEP 封装上报；公钥一致性 409 中止 apply |

### 安全
| 事项 | 说明 |
| --- | --- |
| token 哈希存储 | 所有 token 只存 SHA-256，明文永不落库 |
| token 过期与轮换 | `ttl_seconds`、`/admin/agent-tokens/{id}/rotate` |
| 注册审批闸门 | `pending_registrations` + `/admin/registrations`；rejected 一律 403、pending 不发 token |
| enrollment token 按节点校验 | 哈希存储、绑定节点 / 过期 / 一次性消费 |
| Admin API fail-closed | 未配 `DN42_CONTROL_ADMIN_TOKEN` 整体 403 |
| Admin 审计日志 | `admin_audit_log` append-only + `/admin/audit-log` |

## 1.0 后已补齐

| 事项 | 说明 |
| --- | --- |
| generation 读取 / 回滚 / diff | `GET /admin/nodes/{id}/generations/{gen}`（单代全量快照）、`/diff?against=`（字段级变更列表，缺省比上一代）、`POST .../rollback`（把目标代快照重发为新一代并广播）。回滚只重放快照、不回退子表，后续触发 materialize 的写入会覆盖（见 services/generations.py） |
| Agent reconcile 指标 + `--doctor` | 常驻进程每轮收敛累计写 `<node_dir>/metrics.json`（次数/失败/连续失败/最近状态·时长·世代）；`agent --doctor` 一次性自检配置/状态目录/身份/控制面/Docker/指标，critical 项决定退出码。HTTP 自身 healthz 端点仍未做 |
| Agent token 形状强制（字面量） | 管理面创建 enrollment / agent token 时，运维显式指定的字面量 `token` 强制 base64url + 最小熵长度（400 拒弱口令）。自动生成 token 与鉴权路径不变，待生产 token 格式统一后再扩展到 resolve 边界 |
| 管理 Web UI + 一键互联向导 | 独立静态 SvelteKit（`apps/web`），登录填控制面地址 + token；节点详情各页签 + **概览的「一键互联」分步向导**（无需手写 JSON 建 peering+接口+多会话）。操作指南 [web-ui.md](web-ui.md) |
| 内部互联（iBGP/OSPF）可视化 + 一致性 | `GET /admin/nodes/{id}/internal-topology` 合成视图 + UI「内部互联」页；fleet 不变量「各节点 `internal_topology.routers/hosts` 必须一致」与排错 postmortem 见 [internal-interconnect.md](internal-interconnect.md) |

## 1.0 后

| 事项 | 说明 |
| --- | --- |
| 多副本控制面 | EventBus 换 Redis Pub/Sub + 默认 Postgres，支持控制面横向扩展（当前单进程 MVP，agent 周期 reconcile 兜底） |
| Worker 后台任务 | 当前已移除空骨架；如需 registry 同步 / ROA 同步 / 告警等周期任务再引入（generation 清理已并入 materialize，RPKI 走 stayrtr 容器自拉） |
| Admin RBAC / 多管理员 | 当前单 admin token |
| 批量 / fleet 重物化 | 模板升级后一键全节点重物化 |
| per-service toggle | 可选叶子服务（dns / looking-glass / debug-shell）的单独启用/禁用端点（核心角色 schema 强制，不可禁用） |
| Agent 自身 healthz HTTP 探针 | 指标已落盘 + doctor 已就绪；如需被外部探活再补一个本地 HTTP 端点 |
| Agent token 形状强制（鉴权边界） | 自动生成 token 形如 `<id>.<secret>`（含点号）与 base64url 字面量不同形，统一生产格式后再在 resolve/鉴权侧强制 |
| 更多 runtime backend | Docker Engine 原生 adapter、纯 systemd 网络管理等 |

## 文档
入口 `docs/README.md`，按"教程 / 运维 / 参考 / 内部原理"分层。
