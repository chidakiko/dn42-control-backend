# 数据库

Control Server 使用 SQLAlchemy async ORM 管理数据库。数据库保存控制平面的事实数据（节点、peering、接口、BGP session、DNS zone、token）、已发布的 `DesiredState` generation，以及 Agent 上报的运行状态。

## 入口文件

| 文件 | 说明 |
| --- | --- |
| `apps/control-server/app/db/base.py` | SQLAlchemy `Base` 与命名约定 |
| `apps/control-server/app/db/engine.py` | `Database`，封装 `AsyncEngine` 和 `AsyncSession` |
| `apps/control-server/app/db/seed.py` | `seed_bootstrap_node` 开启时在空库写入 hkg1 示例节点 |
| `apps/control-server/app/db/provision.py` | `provision_node_from_state()`：整节点幂等落库 |
| `apps/control-server/app/db/models/node.py` | `Node`、`EnrollmentToken`、`AgentToken`、`PendingRegistration` |
| `apps/control-server/app/db/models/peering.py` | `Peering`、`WgInterface`、`BgpSession` |
| `apps/control-server/app/db/models/dns.py` | `DnsZone` |
| `apps/control-server/app/db/models/generation.py` | `Generation` |
| `apps/control-server/app/db/models/node_status.py` | `NodeStatus`、`NodeStatusEvent` |

## 配置

数据库连接串来自环境变量 `DN42_CONTROL_DATABASE_URL`（完整配置参考见 [configuration.md](configuration.md)）。

默认值指向仓库根目录下的 `control.db`：

```text
sqlite+aiosqlite:///<repo-root>/control.db
```

PostgreSQL / MySQL 示例：

```bash
export DN42_CONTROL_DATABASE_URL="postgresql+asyncpg://user:pass@localhost/dn42_control"
export DN42_CONTROL_DATABASE_URL="mysql+asyncmy://user:pass@localhost/dn42_control"
```

## 生命周期

FastAPI lifespan 创建 `Database` 并挂到 `app.state.database`。应用启动时会调用 `Base.metadata.create_all(...)`；当 `DN42_CONTROL_SEED_BOOTSTRAP_NODE` 开启**且库为空**时执行 `seed_initial_data(...)`。应用退出时调用 `database.dispose()`。

`create_all` 适合本地开发和测试。生产环境应使用 Alembic 管理 schema 变更。

```mermaid
flowchart TD
    cfg[ControlServerConfig.database_url]
    db[Database]
    engine[create_async_engine]
    session[async_sessionmaker]
    api[API request]
    ok[commit]
    fail[rollback]
    close[close session]

    cfg --> db --> engine --> session --> api
    api -->|正常退出| ok --> close
    api -->|异常退出| fail --> close
```

## 表关系

```mermaid
erDiagram
    nodes ||--o{ agent_tokens : owns
    nodes ||--o{ enrollment_tokens : assigned_to
    nodes ||--o{ generations : publishes
    nodes ||--o{ peerings : local_node
    nodes ||--o{ wg_interfaces : has
    nodes ||--o{ bgp_sessions : has
    nodes ||--o{ dns_zones : has
    nodes ||--|| node_status : reports
    nodes ||--o{ node_status_events : logs
    peerings ||--o{ wg_interfaces : groups
    peerings ||--o{ bgp_sessions : groups
    nodes ||--o{ peerings : remote_node
```

`pending_registrations` 不关联 `nodes`：它记录的是**尚未成为节点**的注册申请。`admin_audit_log` 同样独立：它记录 Admin HTTP 写操作，与具体节点无外键关系。

删除行为：

| 关系 | 外键行为 |
| --- | --- |
| `agent_tokens.node_id -> nodes.node_id` | `ON DELETE CASCADE` |
| `enrollment_tokens.node_id -> nodes.node_id` | `ON DELETE SET NULL` |
| `generations.node_id -> nodes.node_id` | `ON DELETE CASCADE` |
| `peerings.local_node_id -> nodes.node_id` | `ON DELETE CASCADE` |
| `peerings.remote_node_id -> nodes.node_id` | `ON DELETE SET NULL` |
| `wg_interfaces.node_id -> nodes.node_id` | `ON DELETE CASCADE` |
| `wg_interfaces.peering_id -> peerings.id` | `ON DELETE SET NULL` |
| `bgp_sessions.node_id -> nodes.node_id` | `ON DELETE CASCADE` |
| `bgp_sessions.peering_id -> peerings.id` | `ON DELETE SET NULL` |
| `dns_zones.node_id -> nodes.node_id` | `ON DELETE CASCADE` |
| `node_status.node_id -> nodes.node_id` | `ON DELETE CASCADE` |
| `node_status_events.node_id -> nodes.node_id` | `ON DELETE CASCADE` |

## 表说明

### nodes

保存节点基础信息和 `DesiredState` 中不来自子表的部分。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `node_id` | `String(64)` | 主键，节点稳定 ID |
| `site` | `String(32)` | 站点标识 |
| `asn` | `Integer` | 本节点 ASN |
| `router_id` | `String(64)` | BIRD router ID |
| `loopback_ipv4` | `String(64)` | loopback IPv4 |
| `loopback_ipv6` | `String(64)` | loopback IPv6 |
| `ipv4_prefixes` | `JSON` | IPv4 prefix 列表 |
| `ipv6_prefixes` | `JSON` | IPv6 prefix 列表 |
| `inventory` | `JSON` | Agent 注册时上报的主机信息 |
| `labels` | `JSON` | 自定义标签 |
| `base_template` | `JSON` | `runtime`、`bird`、`templates`、`lookglass`、`dns` 等顶层骨架 |
| `current_generation` | `Integer` | 已发布的最新 generation；`0` 表示未发布 |
| `wireguard_public_key` | `String(64)` 或 null | 节点 WG 公钥（agent 上报，自本地私钥推导）。一节点一把私钥，所有 peer 共用；注册一致性校验的权威事实，并被传播进所有"对端是本节点"的 peer 配置 |
| `wireguard_private_key_escrow` | `Text` 或 null | 节点 WG 私钥经恢复公钥 RSA-OAEP 封装的密文；控制面只存不解，仅离线恢复私钥可解封。详见 [security.md](security.md#secret-引用与-wireguard-私钥托管) |
| `created_at` / `updated_at` | `DateTime(timezone=True)` | 创建 / 更新时间 |

### enrollment_tokens

保存 Agent 首次注册使用的一次性 token。与 `agent_tokens` 同安全模型：明文 secret 仅在创建响应中出现一次，DB 只存哈希。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `token` | `String(128)` | 主键，非机密查找键 id（`ent_*`） |
| `token_hash` | `String(128)` | 完整 secret 的 SHA-256，唯一索引；注册校验仅走此列 |
| `node_id` | `String(64)` 或 null | 可选绑定节点；绑定后只对该节点的注册有效 |
| `description` | `String(256)` | 描述 |
| `expires_at` | `DateTime(timezone=True)` | 过期时间 |
| `used_at` | `DateTime(timezone=True)` | 消费时间；非空表示已换取过 agent token，不可再用 |
| `created_at` | `DateTime(timezone=True)` | 创建时间 |

### admin_audit_log

append-only 的 Admin 写操作审计（由 HTTP 中间件写入，不自动裁剪）。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `Integer` | 主键，自增 |
| `actor` | `String(64)` 或 null | 鉴权主体（单 token 模型下为 `admin`）；鉴权失败的尝试为 null |
| `method` | `String(8)` | HTTP 方法（POST / PUT / PATCH / DELETE） |
| `path` | `String(512)` | 请求路径 |
| `status_code` | `Integer` | 响应状态码 |
| `detail` | `JSON` | 附加信息（如 query string） |
| `created_at` | `DateTime(timezone=True)` | 记录时间 |

### agent_tokens

保存 Agent Bearer token。明文 secret 永不落库，校验只走哈希。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `token` | `String(128)` | 主键，非机密查找 id（形如 `agt_xxxxxx`；固定字面量 token 的 id 从哈希确定性派生） |
| `token_hash` | `String(128)`，非空唯一索引 | 完整 secret 的 SHA-256 |
| `node_id` | `String(64)` | token 绑定节点 |
| `agent_id` | `String(128)` | Agent ID |
| `issued_at` | `DateTime(timezone=True)` | 签发时间 |
| `expires_at` | `DateTime(timezone=True)` 或 null | 过期时间；null 表示长期有效 |
| `revoked_at` | `DateTime(timezone=True)` 或 null | 撤销时间 |

### pending_registrations

保存未知节点的注册申请，等待管理员审批（见 [api.md](api.md#注册审批)）。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `Integer` | 自增主键 |
| `requested_node_id` | `String(64)`，索引 | 申请绑定的节点 ID |
| `hostname` | `String(255)` | 申请方主机名 |
| `inventory` | `JSON` | 注册时上报的 `HostInventory` |
| `status` | `String(16)`，索引 | `pending` / `approved` / `rejected` |
| `note` | `String(256)` | 审批备注 |
| `created_at` / `updated_at` | `DateTime(timezone=True)` | 创建 / 更新时间 |

### peerings

保存一条对等关系的元信息。它可以关联 0 到多条接口和 BGP session。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `Integer` | 自增主键 |
| `local_node_id` | `String(64)` | 本地节点 |
| `remote_node_id` | `String(64)` 或 null | 远端节点 |
| `name` | `String(64)` | 对等关系名，节点内唯一 |
| `remote_asn` | `Integer` | 远端 ASN |
| `remote_label` | `String(128)` | 远端可读标签 |
| `is_internal` | `Boolean` | 是否内部对等 |
| `enabled` | `Boolean` | 是否启用 |
| `notes` | `String(512)` | 备注 |
| `created_at` / `updated_at` | `DateTime(timezone=True)` | 创建 / 更新时间 |

### wg_interfaces

保存节点接口资源。表名包含 `wg`，但 `spec` 使用通用 `InterfaceSpec`，可表达 WireGuard、dummy 等接口。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `Integer` | 自增主键 |
| `node_id` | `String(64)` | 所属节点 |
| `peering_id` | `Integer` 或 null | 关联 peering |
| `name` | `String(64)` | 接口名，节点内唯一 |
| `kind` | `String(32)` | 接口类型 |
| `enabled` | `Boolean` | 是否进入 `DesiredState.interfaces` |
| `spec` | `JSON` | 完整 `InterfaceSpec` |
| `sort_order` | `Integer` | materializer 输出排序 |

### bgp_sessions

保存节点 BGP session。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `Integer` | 自增主键 |
| `node_id` | `String(64)` | 所属节点 |
| `peering_id` | `Integer` 或 null | 关联 peering |
| `name` | `String(64)` | session 名，节点内唯一 |
| `remote_asn` | `Integer` | 远端 ASN |
| `enabled` | `Boolean` | 是否启用 |
| `spec` | `JSON` | 完整 `BgpSessionSpec` |
| `sort_order` | `Integer` | materializer 输出排序 |

### dns_zones

保存节点 DNS zone。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `Integer` | 自增主键 |
| `node_id` | `String(64)` | 所属节点 |
| `name` | `String(255)` | zone 名 |
| `spec` | `JSON` | 完整 `DnsZoneSpec` |
| `enabled` | `Boolean` | 是否进入 `DesiredState.dns.zones` |
| `created_at` / `updated_at` | `DateTime(timezone=True)` | 创建 / 更新时间 |

### generations

保存已经发布的完整 `DesiredState` 快照。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `Integer` | 自增主键 |
| `node_id` | `String(64)` | 所属节点 |
| `generation` | `Integer` | 节点内递增世代 |
| `snapshot` | `JSON` | 完整 `DesiredState` |
| `reason` | `String(256)` | 发布原因 |
| `published_at` | `DateTime(timezone=True)` | 发布时间 |

唯一约束：`UNIQUE(node_id, generation)`。

### node_status

每节点一行的最新运行状态汇总，由 `NodeStatusStore` 在 Agent 上报时 upsert。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `node_id` | `String(64)` | 主键，所属节点 |
| `desired_generation` | `Integer` 或 null | 期望 generation |
| `observed_generation` | `Integer` 或 null | 实际观察 generation |
| `last_report_status` | `String(32)` | 最近一次对账状态 |
| `last_apply_status` | `String(32)` | 最近一次 apply 状态 |
| `drift_count` | `Integer` | 最近一次报告的 drift 数 |
| `health` | `String(16)` | `ok` / `stale` / `degraded` / `unknown` |
| `last_snapshot` / `last_report` / `last_apply` | `JSON` | 最近一次完整上报 payload |
| `last_snapshot_at` / `last_report_at` / `last_apply_at` | `DateTime(timezone=True)` | 上报时间 |
| `updated_at` | `DateTime(timezone=True)` | 更新时间 |

### node_status_events

每次 Agent 上报追加一条事件，供 `GET /admin/nodes/{node_id}/status-events` 查询。每节点最多保留最近 100 条。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `Integer` | 自增主键 |
| `node_id` | `String(64)`，索引 | 所属节点 |
| `kind` | `String(16)`，索引 | `snapshot` / `report` / `apply` |
| `generation` | `Integer` 或 null | 上报中的 generation |
| `status` | `String(32)` | 上报状态 |
| `payload` | `JSON` | 完整上报内容 |
| `created_at` | `DateTime(timezone=True)`，索引 | 上报时间 |

## Materializer 写入路径

```mermaid
sequenceDiagram
    participant API as Admin API
    participant DB as 数据库
    participant M as materialize
    participant S as DesiredState schema

    API->>DB: 写入 nodes/interfaces/bgp/dns
    API->>M: materialize(node_id)
    M->>DB: 读取 Node
    M->>DB: 读取 WgInterface/BgpSession/DnsZone
    M->>S: DesiredState.model_validate(snapshot)
    S-->>M: DesiredState
    M->>DB: INSERT generations
    M->>DB: UPDATE nodes.current_generation
```

合成顺序：

1. 读取 `Node`。
2. 新 generation 等于 `Node.current_generation + 1`。
3. 复制 `Node.base_template`。
4. 用 `Node` 字段覆盖 `snapshot.node`。
5. enabled `WgInterface` 按 `sort_order, id` 写入 `interfaces`。
6. `BgpSession` 按 `sort_order, id` 写入 `bgp_sessions`，行级 `enabled` 覆盖 spec。
7. enabled `DnsZone` 按 `name, id` 写入 `dns.zones`。
8. 调用 `DesiredState.model_validate(...)`。
9. 写入 `generations`。
10. 更新 `nodes.current_generation`。

materialize 本身不发布事件；事务提交后由 API 路由调用 `EventBus` 推送 `desired_state_updated`。

**并发安全**：materialize 读取 `Node` 行时加 `SELECT ... FOR UPDATE` 行锁，并发的 admin 写在此处按节点串行化，保证 generation 严格单调递增；`UNIQUE(node_id, generation)` 作为兜底约束。SQLite 忽略 `FOR UPDATE`，但其单写者模型 + 唯一约束保证数据完整（极端并发下后写请求失败回滚，可重试）；生产环境建议 PostgreSQL。

## Provision 写入路径

`provision_node_from_state(session, state, agent_token=None, reason=...)` 是整节点导入的入口，被 seed、`POST /admin/provision` 和 `scripts/tools/import_node_config.py` 复用：

1. 把 `DesiredState` 拆成 `Node.base_template`（去掉 `interfaces` / `bgp_sessions` / `generation`）和子表数据；DNS 骨架留在 `base_template.dns`，zones 拆到 `dns_zones` 行。
2. 创建或更新 `Node`。
3. 删除该节点旧的 `WgInterface` / `BgpSession` / `DnsZone` 行，写入新行。
4. 可选写入固定 `agent_token`。
5. 调用 `materialize()` 发布新一代。

同一 `node_id` 重复 provision 会覆盖旧状态，幂等。

## DesiredStateStore 读取路径

```mermaid
flowchart LR
    id[node_id]
    node[nodes]
    check{current_generation == 0?}
    gen[generations]
    snap[Generation.snapshot]
    validate[DesiredState.model_validate]
    ret[返回 DesiredState]
    none[返回 None]

    id --> node --> check
    check -->|是| none
    check -->|否| gen --> snap --> validate --> ret
```

## Seed 数据

seed 是**显式开关**：只有设置 `DN42_CONTROL_SEED_BOOTSTRAP_NODE=1` 且库为空时，`seed_initial_data(session, config)` 才会写入 hkg1 示例节点（内部走 `provision_node_from_state`，并绑定 `DN42_CONTROL_BOOTSTRAP_AGENT_TOKEN`）。默认行为是启动即空库，节点数据由导入 / provision 流程写入。

## Alembic

配置文件：

```text
alembic.ini
migrations/env.py
migrations/versions/
```

迁移版本：

| 版本 | 内容 |
| --- | --- |
| `a0043f410bda_initial_schema.py` | 初始 schema：`nodes`、`agent_tokens` 等 |
| `b1f2c3d4e5a6_node_runtime_status.py` | 新增 `node_status` 运行状态表 |
| `c2a3b4c5d6e7_token_hash_pending_registrations.py` | `agent_tokens` 增加 `token_hash` / `expires_at`，新增 `pending_registrations` |
| `d4e5f6a7b8c9_admin_audit_enrollment_hash.py` | 新增 `admin_audit_log`；`enrollment_tokens` 增加 `token_hash`（存量明文回填为哈希 + 派生 id） |

执行迁移：

```bash
cd dn42-control-backend
export DN42_CONTROL_DATABASE_URL="sqlite+aiosqlite:///./control.db"
alembic upgrade head
```

生成迁移：

```bash
alembic revision --autogenerate -m "describe change"
```

回滚一个版本：

```bash
alembic downgrade -1
```

## 开发约束

| 约束 | 原因 |
| --- | --- |
| 跨组件协议字段先定义在 `dn42_schemas` | Control Server 和 Agent 必须共享同一结构 |
| 业务表使用"索引列 + `spec` JSON" | 兼顾查询约束和 schema 演进 |
| 写入 `generations.snapshot` 前必须经过 Pydantic 校验 | 保证 Agent 读到的状态合法 |
| Agent 读取 `generations.snapshot`，不是直接读业务表 | 保证发布状态稳定、可追溯 |
| 需要查询或唯一约束的字段单独成列 | 例如 `node_id`、`name`、`remote_asn`、`enabled` |
| 新签发 agent token 只存哈希 | 数据库泄露不暴露可用凭据 |
