# API 参考

Control Server 提供三组接口：

| 分组 | 前缀 | 调用方 | 认证 |
| --- | --- | --- | --- |
| Agent HTTP API | `/api/v1/agent/...` | Node Agent | Bearer token（`register` 除外） |
| Agent WebSocket API | `/api/v1/agent/ws[/{node_id}]` | Node Agent | Bearer token |
| Admin API | `/api/v1/admin/...` | 管理员 / 自动化脚本 | Bearer token（`DN42_CONTROL_ADMIN_TOKEN`），未配置时整体 403（见 [security.md](security.md)） |

基础地址（本地开发）：

```text
http://127.0.0.1:8000
```

健康检查：

```text
GET /healthz
GET /api/v1/healthz
```

OpenAPI：浏览器访问 `/docs`，机器读取 `/openapi.json`。

## 认证模型

### Agent token

除 `POST /api/v1/agent/register` 外，所有 Agent API 都要求：

```text
Authorization: Bearer <agent-token>
```

token 绑定到一个 `node_id`，agent 只能读写自己节点的数据；payload 中 `node_id` 与 token 绑定节点不一致时返回 `403`。

token 形态（见 [security.md](security.md#agent-token-哈希存储)）：

- 新签发 token 格式为 `<token_id>.<secret>`（`token_id` 形如 `agt_xxxxxx`），数据库只存 SHA-256 哈希，**完整 secret 只在签发或轮换响应中出现一次**。
- bootstrap / provision 指定的固定字面量 token 同样只存哈希，主键为派生 id。
- token 可设置过期时间（`expires_at`），过期后解析失败返回 `401`。

### Enrollment token

Agent 首次注册时在请求体中携带 `enrollment_token`，两类均可：

- **全局 bootstrap token**：与 `DN42_CONTROL_ENROLLMENT_TOKEN` 比对；任意节点可用、可重复使用。环境变量设为空字符串可整体关闭。
- **表内一次性 token**：由 `POST /admin/enrollment-tokens` 签发（哈希存储，明文仅创建响应可见）。绑定了 `node_id` 的只对该节点有效；可设过期；成功换取 agent token 后立即消费失效。

### Admin token

所有 `/api/v1/admin/*` 请求要求 `Authorization: Bearer <DN42_CONTROL_ADMIN_TOKEN>`：

- 未配置 `DN42_CONTROL_ADMIN_TOKEN` 时 admin API fail-closed，一律 `403`。
- Bearer 缺失或错误返回 `401`；agent token 不能访问 admin API。
- 所有 admin 写操作（含鉴权失败的尝试）记入 `admin_audit_log`，详见 [security.md](security.md#admin-api-保护)。

## Agent HTTP API

```text
POST /api/v1/agent/register
GET  /api/v1/agent/desired-state
GET  /api/v1/agent/recovery-public-key
POST /api/v1/agent/wireguard-keys
POST /api/v1/agent/runtime-snapshot
POST /api/v1/agent/reconciliation-report
POST /api/v1/agent/apply-result
POST /api/v1/agent/routing-table
WS   /api/v1/agent/ws/{node_id}
```

### POST /api/v1/agent/routing-table

Agent 周期上报 BIRD 路由全表（`RoutingTableSnapshot`：`node_id` / `captured_at` /
`observation` / `routes[]`）。**独立于 reconcile** 的纯观测，不参与对账、不影响
`applied_generation`。`observation` 取 `observed` / `unavailable` / `not-observed`
三态；非 `observed` 时控制面只更新状态、保留上一份已知全表。每条 `route` 含
`prefix` / `origin_asn` / `as_path` / `next_hop` / `protocol` / `primary` / `rpki`。
采集节奏由 agent 的 `routing_interval_seconds`（默认 300，0 关闭）控制。

### GET /api/v1/agent/recovery-public-key

分发离线托管的恢复公钥（PEM）+ SHA-256 指纹，供节点封装本端 WG 私钥。未配置
（`DN42_CONTROL_RECOVERY_PUBLIC_KEY` 为空）时返回 `{"configured": false}`，节点跳过托管。
公钥非秘密；真实性靠 TLS 保证。详见 [security.md](security.md#secret-引用与-wireguard-私钥托管)。

### POST /api/v1/agent/wireguard-keys

节点上报其**唯一** WG 公钥 + 托管密文（`WireGuardKeyReport`，一节点一把私钥），
触发**注册一致性校验**：

- 首次上报 → 登记公钥/密文（`stored`）。
- 与记录公钥一致 → 放行（`matched`），刷新托管密文。
- 与记录公钥不一致 → **`409` 拒绝、事务回滚**；节点不得用偏离密钥拉隧道。

公钥首次登记还会**自动向对端传播**：所有 `peering.remote_node_id == 本节点` 的
WireGuard 接口，其 `wireguard_peer.public_key` 被回填为该公钥，对端节点重新物化并
广播（响应 `propagated_to` 列出这些节点）。外部 peer（`remote_node_id` 为空）不受影响。

`wireguard_public_key` / `wireguard_private_key_escrow` 存于 `nodes` 表，不进本节点
`DesiredState`。token 绑定节点须与 `node_id` 一致，否则 `403`。

### POST /api/v1/agent/register

Agent 首次接入时调用，不需要 Bearer token。

请求体（`AgentRegistrationRequest`）：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `enrollment_token` | string | 是 | 注册 token |
| `requested_node_id` | string | 是 | 要绑定的节点 ID；节点身份必须显式声明，控制面不做猜测绑定 |
| `inventory` | `HostInventory` | 是 | Agent 采集到的主机信息 |

`inventory` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `hostname` | string | 主机名 |
| `os` | string | 操作系统 |
| `arch` | string | CPU 架构 |
| `kernel` | string 或 null | kernel 版本 |
| `container_runtime` | string 或 null | 容器运行时 |
| `container_runtime_version` | string 或 null | 容器运行时版本 |
| `has_systemd` | boolean | 是否有 systemd |
| `capabilities` | string[] | 节点能力，例如 `docker`、`wireguard`、`bird` |
| `labels` | object | 自定义标签 |

请求示例：

```json
{
  "enrollment_token": "enroll-token",
  "requested_node_id": "edge1",
  "inventory": {
    "hostname": "edge1",
    "os": "linux",
    "arch": "amd64",
    "kernel": "6.8.0",
    "container_runtime": "docker",
    "container_runtime_version": "26.1.0",
    "has_systemd": true,
    "capabilities": ["docker", "wireguard", "bird"],
    "labels": {"site": "hkg"}
  }
}
```

注册分支逻辑：

1. `enrollment_token` 校验：既不是全局 bootstrap token，也不是表内有效（未过期、未消费、节点绑定匹配）token → `401`。
2. 审批门禁：该节点最近一条注册申请为 `rejected` → `403`，**即使节点已被 provision**。
3. 未知节点 → 写入 / 刷新 `pending_registrations` 待审批记录，返回 `pending-approval`，**不签发 token**（已 `approved` 等待 provision 的不会被顶回 pending）。管理员审批与 provision 流程见 [operations.md](operations.md#新节点接入与审批)。
4. 已 provision 但审批仍为 `pending` → 返回 `pending-approval`，不签发 token。
5. 已 provision 且审批通过（或无审批记录，即管理员直接 provision）→ 签发 agent token，返回 `accepted`；使用的表内 enrollment token 随即被消费。

接受响应：

```json
{
  "status": "accepted",
  "node_id": "edge1",
  "agent_id": "edge1-agent",
  "agent_token": "agt_3f2a1b.5kJh...one-time-secret...",
  "desired_state_generation": 1,
  "message": null
}
```

待审批响应：

```json
{
  "status": "pending-approval",
  "node_id": "new-node",
  "agent_id": "new-node-agent",
  "agent_token": null,
  "desired_state_generation": null,
  "message": "node not provisioned; registration pending admin approval"
}
```

错误：

| 状态码 | 场景 |
| --- | --- |
| `401` | `enrollment_token` 无效 / 过期 / 已消费 / 与请求节点不匹配 |
| `403` | 该节点的注册申请已被管理员 reject |
| `422` | 请求体不符合 schema |

### GET /api/v1/agent/desired-state

Agent 拉取自身节点的最新 `DesiredState`（字段说明见 [desired-state.md](desired-state.md)）。

```bash
curl -s \
  "http://127.0.0.1:8000/api/v1/agent/desired-state" \
  -H "Authorization: Bearer <agent-token>" | jq
```

错误：

| 状态码 | 场景 |
| --- | --- |
| `401` | Bearer token 缺失、无效或已过期 |
| `404` | token 对应节点没有已发布 `DesiredState` |

### POST /api/v1/agent/runtime-snapshot

Agent 上报实际观察到的 runtime 状态（`RuntimeSnapshot`）。控制面把它持久化到 `node_status` / `node_status_events`，并据此推导节点健康（见 [Admin 健康接口](#健康与状态事件)）。

请求体：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `node_id` | string | 上报节点 ID，必须等于 token 绑定节点 |
| `generation` | integer 或 null | agent 当前已成功应用的 generation（来自本地 identity） |
| `captured_at` | string | ISO-8601 时间 |
| `containers` | object[] | 容器状态（name/role/config_hash/status/healthy） |
| `interfaces` | object[] | 网络接口状态 |
| `wireguard_interfaces` | object[] | WireGuard 状态（name/listen_port/peer_count/status） |
| `bgp_protocols` | object[] | BIRD/BGP protocol 状态（name/session/state/info） |
| `errors` | string[] | 采集错误 |

响应：

```json
{
  "accepted": true,
  "node_id": "edge1",
  "generation": 3,
  "containers": 1,
  "interfaces": 0
}
```

### POST /api/v1/agent/reconciliation-report

Agent 上报期望状态和实际状态的对账结果（`ReconciliationReport`）。

请求示例：

```json
{
  "node_id": "edge1",
  "desired_generation": 3,
  "observed_generation": 2,
  "status": "degraded",
  "captured_at": "2026-06-08T07:04:05+00:00",
  "drift": [
    {
      "component": "container",
      "name": "dn42-edge1-dn42-bird-router-1",
      "severity": "critical",
      "message": "container generation is stale",
      "desired": "3",
      "observed": "2"
    }
  ]
}
```

响应：

```json
{
  "accepted": true,
  "node_id": "edge1",
  "status": "degraded",
  "drift_items": 1
}
```

### POST /api/v1/agent/apply-result

Agent 上报一次 apply 尝试（`ApplyResult`）。

请求示例：

```json
{
  "node_id": "edge1",
  "generation": 3,
  "status": "succeeded",
  "started_at": "2026-06-08T07:04:04+00:00",
  "finished_at": "2026-06-08T07:04:04+00:00",
  "plan_summary": {"create": 0, "update": 4, "delete": 0, "noop": 19},
  "applied_files": [
    {"action": "update", "path": "bird/dn42_peers.conf", "sha256": "..."}
  ],
  "errors": []
}
```

响应：

```json
{
  "accepted": true,
  "node_id": "edge1",
  "generation": 3,
  "status": "succeeded"
}
```

## Agent WebSocket API

WebSocket 是"门铃"通道：只通知"有新事件"，不传完整业务数据。Agent 收到事件后仍通过 HTTP 拉取完整 `DesiredState`。

规范路径带上目标节点，实现按节点隔离的私有通道：

```text
WS /api/v1/agent/ws/{node_id}
```

服务端校验 token 解析出的 `node_id` 与路径一致，不一致即拒绝。这是唯一的 WS 路径，没有不带 `node_id` 的变体。

连接请求：

```text
GET /api/v1/agent/ws/edge1
Authorization: Bearer <agent-token>
Upgrade: websocket
```

鉴权失败：

```text
close code: 4401   # token 缺失、无效或过期
close code: 4403   # token 合法但与路径里的 node_id 不匹配
```

事件消息（连接成功后第一条总是 `hello`）：

```json
{"type": "hello", "node_id": "edge1", "generation": 1}
{"type": "desired_state_updated", "generation": 2, "reason": "interface updated"}
{"type": "snapshot_request", "reason": "operator requested"}
```

`desired_state_updated.reason` 是控制面对"这次为什么变"的可读描述，仅供日志排错；agent 的收敛判定完全基于本地差异对比，不依赖该字段。

事件投递语义：每条连接对应一个有限队列（64 条），队列满时丢弃事件——agent 依靠兜底周期 reconcile（默认 300 秒）补偿，见 [node-agent.md](node-agent.md#常驻守护进程模式默认)。

Python 客户端示例：

```python
import asyncio
import websockets


async def main():
    async with websockets.connect(
        "ws://127.0.0.1:8000/api/v1/agent/ws/edge1",
        additional_headers={"Authorization": "Bearer <agent-token>"},
    ) as ws:
        async for message in ws:
            print(message)


asyncio.run(main())
```

## Admin API 总览

```text
# 节点
GET    /api/v1/admin/nodes
POST   /api/v1/admin/nodes
GET    /api/v1/admin/nodes/{node_id}
PATCH  /api/v1/admin/nodes/{node_id}
DELETE /api/v1/admin/nodes/{node_id}              # 已发布的 active 节点须先 decommission，否则 409
GET    /api/v1/admin/nodes/{node_id}/desired-state
GET    /api/v1/admin/nodes/{node_id}/generations?limit=50
GET    /api/v1/admin/nodes/{node_id}/generations/{gen}          # 单代完整快照
GET    /api/v1/admin/nodes/{node_id}/generations/{gen}/diff?against=  # 字段级 diff，缺省比上一代
POST   /api/v1/admin/nodes/{node_id}/generations/{gen}/rollback # 把该代快照重发为新一代并广播
POST   /api/v1/admin/nodes/{node_id}/notify

# 节点退役（停止宣告路由：下发空 interfaces/bgp，agent 拆除隧道与会话）
POST   /api/v1/admin/nodes/{node_id}/decommission
POST   /api/v1/admin/nodes/{node_id}/recommission # 撤销退役，恢复 active 并重物化

# 整节点 provision
POST   /api/v1/admin/provision

# 注册审批
GET    /api/v1/admin/registrations?status=pending
POST   /api/v1/admin/registrations/{registration_id}/approve
POST   /api/v1/admin/registrations/{registration_id}/reject

# 健康与状态事件
GET    /api/v1/admin/health
GET    /api/v1/admin/nodes/{node_id}/health
GET    /api/v1/admin/nodes/{node_id}/status-events?kind=apply&limit=50

# Peering
GET    /api/v1/admin/nodes/{node_id}/peerings
POST   /api/v1/admin/nodes/{node_id}/peerings
POST   /api/v1/admin/nodes/{node_id}/peerings/provision  # 一键化：同事务建 Peering+WgInterface+(可选)BgpSession
GET    /api/v1/admin/peerings/{peering_id}
PATCH  /api/v1/admin/peerings/{peering_id}
DELETE /api/v1/admin/peerings/{peering_id}

# 接口
GET    /api/v1/admin/nodes/{node_id}/interfaces
POST   /api/v1/admin/nodes/{node_id}/interfaces
GET    /api/v1/admin/interfaces/{iface_id}
PATCH  /api/v1/admin/interfaces/{iface_id}
DELETE /api/v1/admin/interfaces/{iface_id}

# BGP session
GET    /api/v1/admin/nodes/{node_id}/bgp-sessions
POST   /api/v1/admin/nodes/{node_id}/bgp-sessions
GET    /api/v1/admin/bgp-sessions/{session_id}
PATCH  /api/v1/admin/bgp-sessions/{session_id}
DELETE /api/v1/admin/bgp-sessions/{session_id}

# DNS zone
GET    /api/v1/admin/nodes/{node_id}/dns-zones
POST   /api/v1/admin/nodes/{node_id}/dns-zones
GET    /api/v1/admin/dns-zones/{zone_id}
PATCH  /api/v1/admin/dns-zones/{zone_id}
DELETE /api/v1/admin/dns-zones/{zone_id}

# Agent token
GET    /api/v1/admin/nodes/{node_id}/agent-tokens
POST   /api/v1/admin/nodes/{node_id}/agent-tokens
POST   /api/v1/admin/agent-tokens/{token_id}/rotate
DELETE /api/v1/admin/agent-tokens/{token_id}

# Enrollment token
GET    /api/v1/admin/enrollment-tokens
POST   /api/v1/admin/enrollment-tokens
DELETE /api/v1/admin/enrollment-tokens/{token_id}

# 审计日志
GET    /api/v1/admin/audit-log?limit=100
```

所有请求需携带 `Authorization: Bearer <DN42_CONTROL_ADMIN_TOKEN>`。

资源类（节点、peering、接口、BGP session、DNS zone）的写操作会触发该节点重新 materialize，生成新的 `DesiredState` generation，并向在线 Agent 推送 `desired_state_updated` 事件。

### POST /api/v1/admin/provision

接受一份**完整 `DesiredState`**，一次性把「节点 + 接口 + BGP session + DNS zone」落库并发布为新一代。与逐资源 CRUD 互补，适合：

- 部署期批量灌入多节点（如三节点 compose 的 provisioner 容器）；
- 从离线渲染好的 `DesiredState` 直接导入控制面；
- 配合 `scripts/tools/import_node_config.py` 导入存量节点。

幂等：同一 `node_id` 重复 provision 会覆盖旧状态并 materialize 新一代，不会报 `409`。

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `state` | object | 是 | 完整 `DesiredState` JSON dump |
| `agent_token` | string 或 null | 否 | 绑定到该节点的固定 agent token，便于联调 |

响应（`201`）：

```json
{
  "node_id": "edge1",
  "generation": 1,
  "subscribers": 1,
  "delivered": 1
}
```

`subscribers` / `delivered` 表示该节点当前 WS 订阅数和本次事件实际投递数。`state` 校验失败返回 `422`。

### 注册审批

未知节点调用 `/agent/register` 后会进入 `pending_registrations` 表。管理员流程：

```bash
# 1. 查看待审批队列
curl -s "http://127.0.0.1:8000/api/v1/admin/registrations?status=pending"

# 2. 批准（或把 approve 换成 reject）
curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/registrations/3/approve" \
  -H "Content-Type: application/json" -d '{"note": "确认是我的机器"}'
```

`status` 取值 `pending` / `approved` / `rejected`；approve/reject 请求体可带可选 `note`。

> **注意**：approve 只是把申请标记为通过，**并不自动 provision、也不签发 token**。真正让节点工作还需要调用 `POST /admin/provision`（或逐资源 CRUD）下发 `DesiredState`，之后该节点的 agent 重新 `register` 才能拿到 token。

### 健康与状态事件

控制面把 Agent 上报的 snapshot / report / apply-result 持久化，并推导节点健康：

| 健康值 | 含义 |
| --- | --- |
| `ok` | 实际状态 = 期望状态 |
| `stale` | 落后：观察 generation 低于期望，或太久没上报（默认 900 秒） |
| `degraded` | 应用失败或检测到 drift |
| `unknown` | 还没收到过任何上报 |

```bash
# 机群概览：{"summary":{"ok":2,"stale":1},"nodes":[...]}
curl -s "http://127.0.0.1:8000/api/v1/admin/health"

# 单节点健康 + 最近一次 snapshot/report/apply
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/health"

# 上报历史（kind 可选 snapshot / report / apply；limit 1-500，默认 50）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/status-events?kind=apply&limit=20"
```

`/nodes/{node_id}/health` 在节点从未上报时返回 `404`。每节点的事件历史最多保留最近 100 条。

### 路由全表分析（Radar 式）

控制面把 Agent 上报的 BIRD 路由全表（`POST /agent/routing-table`）聚合存库，提供
全表统计与时间序列查询。从未上报时各接口返回 `404`。

```bash
# 全表规模 + RPKI / 前缀长度 / AS path 分布 + 按 peer 统计
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/routing/summary"

# 起源 AS Top 榜（limit 1-1000，默认 50）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/routing/origins?limit=20"

# 前缀检索（family=4/6 过滤、q 关键字、limit/offset 分页）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/routing/prefixes?family=6&q=fd42&limit=100"

# 路由表规模趋势 + churn（每次快照相对上次的 announced / withdrawn）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/routing/timeline?limit=200"
```

时间序列每节点最多保留最近 500 条。RPKI 由 Agent 本地按 BIRD 的 ROA 表（`dn42_roa` /
`dn42_roa_v6`）做 RFC 6811 起源校验，三态 `valid` / `invalid` / `not-found`（无法判定即
ROA 整表没采到时不计入任何桶，由 `rpki_observed=false` 显式标记）。明细逐路由落控制面的
`node_route_entries` 索引表，前缀检索走 SQL `WHERE` + 索引，不再整列 JSON 全扫。

### Agent token 管理

token 元信息列表不含 secret；**secret 只在签发 / 轮换响应里出现一次**。

```bash
# 列出某节点所有 token（仅元信息：token id / 签发 / 过期 / 撤销时间）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/agent-tokens"

# 签发新 token，可选 ttl_seconds 设过期；响应 secret 字段是完整 token，仅此一次
curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/agent-tokens" \
  -H "Content-Type: application/json" -d '{"ttl_seconds": 604800}'

# 轮换：撤销旧 token、签发新 token（token_id 即列表里的 token 字段）
curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/agent-tokens/agt_3f2a1b/rotate"

# 撤销
curl -s -X DELETE \
  "http://127.0.0.1:8000/api/v1/admin/agent-tokens/agt_3f2a1b"
```

签发请求体（全部可选）：

| 字段 | 说明 |
| --- | --- |
| `token` | 自定义 token 字面量（bootstrap / 联调场景；同样只存哈希，主键为派生 id） |
| `agent_id` | 自定义 agent ID |
| `ttl_seconds` | 过期秒数（≥1） |

### Enrollment token 管理

```bash
curl -s "http://127.0.0.1:8000/api/v1/admin/enrollment-tokens"

curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/enrollment-tokens" \
  -H "Content-Type: application/json" \
  -d '{"description": "hkg2 onboarding", "node_id": "hkg2-edge"}'

curl -s -X DELETE \
  "http://127.0.0.1:8000/api/v1/admin/enrollment-tokens/<token_id>"
```

创建响应中的 `secret` 是明文注册 token，**仅此一次可见**（DB 只存哈希）；列表 / 删除均以非机密的 `token_id`（`ent_*`）为键。绑定了 `node_id` 的 token 只对该节点的注册有效，成功换取 agent token 后立即消费失效。

## Admin 示例：新增 peer

新增 peering：

```bash
peering='{
  "name": "test-peer-4242429001",
  "remote_asn": 4242429001,
  "remote_label": "test peer",
  "is_internal": false,
  "enabled": true
}'

curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/peerings" \
  -H "Content-Type: application/json" \
  -d "$peering"
```

为节点配置可对外发布的 WireGuard UDP 端口范围：

```bash
node=$(curl -s \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1")

base=$(echo "$node" | jq '.base_template')
base=$(echo "$base" | jq '.runtime.wireguard_port_range = {start: 38000, end: 38020}')

patch=$(jq -n --argjson base "$base" '{base_template: $base}')

curl -s -X PATCH \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1" \
  -H "Content-Type: application/json" \
  -d "$patch"
```

`runtime.wireguard_port_range` 会让生成出的 `router-netns` 服务发布整段 UDP 端口，例如上面的配置会生成 `38000-38020:38000-38020/udp`。新增或修改 WireGuard 接口时，`listen_port` 必须落在这个范围内，并且不能和同一节点上其他已启用 WireGuard 接口重复。

新增接口：

```bash
iface='{
  "peering_id": 1,
  "enabled": true,
  "sort_order": 100,
  "spec": {
    "name": "test-peer-9001",
    "kind": "wireguard",
    "addresses": ["198.18.90.2/31"],
    "peer_routes": ["198.18.90.1/32"],
    "private_key_ref": "secret://edge1/test-peer-9001/private",
    "listen_port": 38001,
    "wireguard_peer": {
      "public_key": "CnSyivWoaOTjTKo/6ydo7VabR3yUU+R7N3Tq7hFyqxg=",
      "allowed_ips": ["198.18.90.1/32"]
    }
  }
}'

curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/interfaces" \
  -H "Content-Type: application/json" \
  -d "$iface"
```

新增 BGP session：

```bash
session='{
  "peering_id": 1,
  "sort_order": 100,
  "spec": {
    "name": "test_peer_4242429001_v4",
    "remote_asn": 4242429001,
    "neighbor": "198.18.90.1",
    "source_address": "198.18.90.2",
    "address_family": "ipv4",
    "interface": "test-peer-9001",
    "protocol_suffix": "_v4",
    "enabled": true
  }
}'

curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/bgp-sessions" \
  -H "Content-Type: application/json" \
  -d "$session"
```

这些写入会触发新的 `DesiredState` generation，并向在线 Agent 发送 `desired_state_updated`。

## Admin 示例：修改接口配置

修改接口前先查询节点现有接口，拿到要修改的 `id`。返回里的 `spec` 是该接口进入 `DesiredState.interfaces` 的完整配置。

```bash
interfaces=$(curl -s \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/interfaces")

echo "$interfaces" | jq '.[] | {id, name, kind, enabled, sort_order}'
```

`PATCH /api/v1/admin/interfaces/{iface_id}` 支持修改这些顶层字段：

| 字段 | 含义 |
|---|---|
| `spec` | 完整 `InterfaceSpec`。传入时会整体替换原接口规格 |
| `enabled` | 是否进入下一代 `DesiredState.interfaces` |
| `sort_order` | 物化到 DesiredState 时的排序权重 |
| `peering_id` | 关联到另一个 peering |
| `clear_peering` | 设为 `true` 时清空 peering 关联 |

下面示例基于现有 `spec` 修改 WireGuard 监听端口、对端 endpoint 和 MTU，然后提交完整 `spec`。如果节点配置了 `runtime.wireguard_port_range`，新的 `listen_port` 必须位于该范围内；如果同一节点已有启用的 WireGuard 接口使用了相同 `listen_port`，请求会被拒绝。

```bash
iface=$(echo "$interfaces" | jq '.[] | select(.name == "as4242429001")')

spec=$(echo "$iface" | jq '.spec
  | .listen_port = 38010
  | .mtu = 1420
  | .wireguard_peer.endpoint = "203.0.113.10:51820"
  | .wireguard_peer.persistent_keepalive_seconds = 25')

patch=$(jq -n --argjson spec "$spec" --argjson sort_order "$(echo "$iface" | jq '.sort_order')" \
  '{spec: $spec, enabled: true, sort_order: $sort_order}')

curl -s -X PATCH \
  "http://127.0.0.1:8000/api/v1/admin/interfaces/$(echo "$iface" | jq -r '.id')" \
  -H "Content-Type: application/json" \
  -d "$patch"
```

如果只是临时停用某个接口，不需要传 `spec`：

```bash
patch='{"enabled": false}'

curl -s -X PATCH \
  "http://127.0.0.1:8000/api/v1/admin/interfaces/$(echo "$iface" | jq -r '.id')" \
  -H "Content-Type: application/json" \
  -d "$patch"
```

修改成功后，Control Server 会重新物化该节点并生成新的 `DesiredState` generation。Agent 下一次 reconcile 会拉取新 generation、重新渲染接口配置，并只在容器 generation 或运行状态不匹配时重建相关容器。

WireGuard 对外端口由节点级 `runtime.wireguard_port_range` 统一发布到 `router-netns` 容器。因为 `wg-gateway` 与 `bird-router` 都共享 `router-netns` 的 network namespace，端口映射应出现在 `router-netns` 服务上，而不是 `wg-gateway` 服务上。

可以用下面的命令确认 generation 和 Agent 应用结果：

```bash
curl -s \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/generations?limit=5"

curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/status-events?kind=apply&limit=5"
```

```bash
journalctl -u dn42-node-agent@edge1.service -n 120 --no-pager
docker exec dn42-edge1-dn42-wg-gateway-1 wg show
```

### 世代历史：查看 / 对比 / 回滚

每一代 `generations.snapshot` 都保存了完整的 `DesiredState`。除了列表，还可以：

```bash
# 单代完整快照
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/generations/7"

# 字段级 diff（缺省比较第 6→7 代；可用 ?against=3 指定基准）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/generations/7/diff"

# 回滚：把第 5 代的快照重新发布为新一代并广播给 agent
curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/generations/5/rollback"
```

`diff` 返回 `{from_generation, to_generation, changed, changes[]}`，每条变更含 `path`（如 `bgp_sessions[0].enabled`）、`op`（`added`/`removed`/`changed`）与 `old`/`new`。

回滚是**紧急逃生阀**，不是配置编辑：它只把目标代的快照重发为新一代（generation 号继续递增），**不**回退 normalized 子表（peerings/wg/bgp/dns）。因此回滚之后，任何会触发重物化的后续管理写入（PATCH 节点/接口/会话等）都会从当前子表重新组装、覆盖这次回滚。要持久改变配置，请改子表再走正常 CRUD 流程。
