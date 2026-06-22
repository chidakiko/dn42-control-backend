# API 参考

本文档面向调用控制面的人：管理员 / 自动化脚本（Admin API）与节点 Agent（Agent HTTP / WebSocket）。每个 HTTP 路由与 WebSocket 端点的方法、路径、请求体、响应体、鉴权与状态码都来自源码（`apps/control-server/app/api/v1/`），不含未实现的端点。

> **基础路径**：除 `/healthz` 外所有业务路由都挂在 `/api/v1` 前缀下（`apps/control-server/app/main.py:93`）。
>
> **鉴权模型**（`apps/control-server/app/api/deps.py`）：
> - **Admin**：`Authorization: Bearer <DN42_CONTROL_ADMIN_TOKEN>`。整个 `/api/v1/admin` 前缀挂 `require_admin`；未配置 admin token 时 fail-closed，整体返回 `403`；Bearer 缺失 / 错误返回 `401`。
> - **Agent**：`Authorization: Bearer <agent token>`。token 绑定到一个 `node_id`，agent 只能读写自己节点；payload 中 `node_id` 与 token 绑定节点不一致返回 `403`。token 解析失败返回 `401`。
> - **注册端点**（`POST /api/v1/agent/register`）：不带 Bearer，改在**请求体**里携带 enrollment token（全局 bootstrap token 或表内一次性 token）。

参见：[配置](../reference/configuration.md) · [DesiredState](../reference/desired-state.md) · [安全模型](../internals/security.md)。

---

## 概览 / 鉴权

| 分组 | 前缀 | 调用方 | 鉴权 |
| --- | --- | --- | --- |
| Admin API | `/api/v1/admin/...` | 管理员 / 自动化脚本 | Admin Bearer（`DN42_CONTROL_ADMIN_TOKEN`），未配置时整体 `403` |
| Agent HTTP API | `/api/v1/agent/...` | Node Agent | Agent Bearer（`/register` 除外） |
| Agent WebSocket API | `/api/v1/agent/ws/{node_id}` | Node Agent | Agent Bearer（握手头） |
| 健康探针 | `/healthz`、`/api/v1/healthz` | 负载均衡 / systemd | 无 |

OpenAPI：浏览器访问 `/docs`，机器读取 `/openapi.json`。

**Token 形态**（详见 [安全模型](../internals/security.md)）：

- Agent token 自动签发为 `<token_id>.<secret>`（`token_id` 形如 `agt_xxxxxx`）；DB 只存 SHA-256 哈希，**完整 secret 只在签发 / 轮换响应中出现一次**。可设过期（`expires_at`），过期后解析返回 `401`。
- Enrollment token 同模型：DB 存哈希，明文 secret 仅创建响应可见；列表 / 删除以非机密 `token_id`（`ent_*`）为键。
- 全局 bootstrap enrollment token 来自配置 `DN42_CONTROL_ENROLLMENT_TOKEN`（不绑定节点、可重复使用；设空字符串可关闭）。

**审计**：所有 `/api/v1/admin/*` 的写请求（`POST` / `PUT` / `PATCH` / `DELETE`，含鉴权失败的尝试）由 `audit_admin_writes` 中间件记入审计日志（`apps/control-server/app/main.py:98`）。

**通用错误约定**：

- `400` 引用了不存在的关联资源（如 peering 的 `remote_node_id`）。
- `404` 路径上的资源不存在。
- `409` 唯一约束冲突（重名 / 已存在），或语义冲突（活节点直删、WG 端口占用、notify 时无在线 agent）。
- `422` schema 校验失败（`InterfaceSpec` / `BgpSessionSpec` / `DesiredState` / DNS 配置等），响应体形如 `{"detail": {"message": "...", "errors": [...]}}`。

---

## Admin API

所有 Admin 路由前缀为 `/api/v1/admin`，下表「路径」列省略该前缀。所有路由需 Admin Bearer。

### 节点（Node）

源码：`apps/control-server/app/api/v1/admin/nodes.py`

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/nodes` | 列出所有节点（按 `node_id` 排序） |
| `POST` | `/nodes` | 创建节点，`201`；`node_id` 已存在 `409` |
| `GET` | `/nodes/{node_id}` | 取单节点 |
| `PATCH` | `/nodes/{node_id}` | 局部更新；已发布过（`current_generation>0`）则同事务重物化并广播 |
| `DELETE` | `/nodes/{node_id}` | 删除，`204`；活节点（已发布且非 decommissioned）`409`，须先退役 |
| `POST` | `/nodes/{node_id}/decommission` | 退役：发布空 DesiredState，拆除隧道 / 撤 BGP，保留节点与子表 |
| `POST` | `/nodes/{node_id}/recommission` | 撤销退役，恢复 active 并重新物化 |
| `GET` | `/nodes/{node_id}/desired-state` | 该节点已发布的完整 DesiredState（JSON）；无则 `404` |
| `GET` | `/nodes/{node_id}/generations?limit=` | 世代历史元信息（`limit` 1..500，默认 50） |
| `GET` | `/nodes/{node_id}/generations/{generation}` | 单代详情（元信息 + 完整快照） |
| `GET` | `/nodes/{node_id}/generations/{generation}/diff?against=` | 两代字段级 diff；`against` 缺省取 `generation-1`，第 1 代须显式传 |
| `POST` | `/nodes/{node_id}/generations/{generation}/rollback` | 把某代快照重新发布为新一代并广播 |
| `POST` | `/nodes/{node_id}/notify` | 手动门铃：`desired_state_updated`（递增世代）或 `snapshot_request`；无在线 agent 订阅时 `409` |

`NodeIn` 字段：`node_id`（1..64）、`asn`（≥1）、`router_id`，可选 `site` / `loopback_ipv4` / `loopback_ipv6` / `ipv4_prefixes[]` / `ipv6_prefixes[]` / `inventory{}` / `labels{}` / `base_template{}`。`NodePatch` 同字段全可选（`exclude_unset` 语义，省略即不动）。`NodeOut` 额外含 `current_generation` / `lifecycle` / `dns_group_id` / `created_at` / `updated_at`。

创建节点示例：

```json
// POST /api/v1/admin/nodes
{
  "node_id": "edge1",
  "asn": 4242420000,
  "router_id": "172.20.0.1",
  "site": "hkg",
  "loopback_ipv4": "172.20.0.1",
  "loopback_ipv6": "fd00:1234::1",
  "ipv4_prefixes": ["172.20.0.0/27"],
  "ipv6_prefixes": ["fd00:1234::/48"],
  "labels": {"region": "apac"}
}
```

```json
// 201 Created
{
  "node_id": "edge1",
  "asn": 4242420000,
  "router_id": "172.20.0.1",
  "site": "hkg",
  "loopback_ipv4": "172.20.0.1",
  "loopback_ipv6": "fd00:1234::1",
  "ipv4_prefixes": ["172.20.0.0/27"],
  "ipv6_prefixes": ["fd00:1234::/48"],
  "inventory": {},
  "labels": {"region": "apac"},
  "base_template": {},
  "current_generation": 0,
  "lifecycle": "active",
  "dns_group_id": null,
  "created_at": "2026-06-22T00:00:00Z",
  "updated_at": "2026-06-22T00:00:00Z"
}
```

`NotifyRequest`：`{ "event": "desired_state_updated" | "snapshot_request", "reason": "..." }`（默认 `desired_state_updated`）。`NotifyResponse`：`{ node_id, event, generation, subscribers, delivered }`。`RollbackResponse`：`{ node_id, target_generation, new_generation, reason, subscribers, delivered }`。

### Peering

源码：`apps/control-server/app/api/v1/admin/peerings.py`。Peering 是对等关系的元信息（不直接进入 DesiredState）；普通 CRUD **不触发 materialize**，`provision` / `full` 才会一并物化子资源。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/nodes/{node_id}/peerings` | 列出节点的 peering（按 `name`） |
| `POST` | `/nodes/{node_id}/peerings` | 创建 peering，`201`；同节点重名 `409` |
| `GET` | `/peerings/{peering_id}` | 取单条 peering |
| `PATCH` | `/peerings/{peering_id}` | 局部更新 peering 元信息 |
| `DELETE` | `/peerings/{peering_id}` | 删除，`204` |
| `POST` | `/nodes/{node_id}/peerings/provision` | 一键：同事务建 Peering + WgInterface +（可选）BgpSession 并物化广播，`201` |
| `GET` | `/nodes/{node_id}/peerings/full` | 组合读：节点全部 peering + 其名下接口 / BGP 会话 |
| `GET` | `/peerings/{peering_id}/full` | 单 peering 的组合读 |
| `PUT` | `/nodes/{node_id}/peerings/full` | 按 `(node_id, peering.name)` create-or-replace 完整 peer（子资源整集替换），`200` |
| `POST` | `/nodes/{node_id}/peerings/backfill` | 把孤儿接口 / 会话归并成 Peering（`dry_run` 仅返回计划，不物化 / 不广播） |

`PeeringIn`：`name`（1..64）、`remote_asn`（≥1），可选 `remote_node_id` / `remote_label` / `is_internal`（默认 `false`）/ `enabled`（默认 `true`）/ `notes`。

provision 一键端点请求 `PeeringProvisionIn`：`peering`（`PeeringIn`）+ `interface_spec`（`InterfaceSpec` 的 JSON dump，见 [DesiredState](../reference/desired-state.md)）+ `interface_enabled` / `interface_sort_order`，可选 `bgp_spec`（`BgpSessionSpec` dump）+ `bgp_sort_order`。三段先各自 schema 校验（失败 `422`），任一唯一约束冲突 `409` 整笔回滚。

```json
// POST /api/v1/admin/nodes/edge1/peerings/provision
{
  "peering": {
    "name": "as4242421111",
    "remote_asn": 4242421111,
    "remote_label": "example-peer",
    "is_internal": false
  },
  "interface_spec": {
    "name": "wg-peer1",
    "kind": "wireguard",
    "listen_port": 51820,
    "addresses": ["fe80::1/64"]
  },
  "bgp_spec": {
    "name": "as4242421111",
    "remote_asn": 4242421111,
    "neighbor_address": "fe80::2",
    "enabled": true
  }
}
```

```json
// 201 Created（节选）
{
  "peering": { "id": 12, "local_node_id": "edge1", "name": "as4242421111", "remote_asn": 4242421111, "...": "..." },
  "interface": { "id": 34, "node_id": "edge1", "name": "wg-peer1", "kind": "wireguard", "...": "..." },
  "bgp_session": { "id": 56, "node_id": "edge1", "name": "as4242421111", "remote_asn": 4242421111, "...": "..." },
  "generation": 5
}
```

`PUT .../peerings/full` 请求 `PeeringFullIn`：`{ peering: PeeringIn, interfaces: [{spec, enabled, sort_order}], bgp_sessions: [{spec, sort_order}] }`；返回 `{ peering: <full>, generation }`。

### 接口（WgInterface）

源码：`apps/control-server/app/api/v1/admin/interfaces.py`。写端点接受完整 `InterfaceSpec`（`spec` 字段），先 schema 校验，写完物化 + 广播。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/nodes/{node_id}/interfaces` | 列出节点接口（按 `sort_order`, `id`） |
| `POST` | `/nodes/{node_id}/interfaces` | 创建接口并物化广播，`201`；同节点重名 `409` |
| `GET` | `/interfaces/{iface_id}` | 取单接口 |
| `PATCH` | `/interfaces/{iface_id}` | 局部更新（可改 `spec` / `enabled` / `sort_order` / `peering_id`），物化广播 |
| `DELETE` | `/interfaces/{iface_id}` | 删除并物化广播，`204` |

`InterfaceIn`：`spec`（`InterfaceSpec` dump）、可选 `peering_id`、`enabled`（默认 `true`）、`sort_order`（默认 0）。`InterfacePatch` 各字段可选，并有 `clear_peering`（显式把 `peering_id` 置 null）。`InterfaceOut`：`{ id, node_id, peering_id, name, kind, enabled, sort_order, spec }`。

WireGuard 端口策略：启用的 WG 接口若设 `listen_port`，须落在节点 `base_template.runtime.wireguard_port_range` 内（越界 `422`），且不得与同节点其他启用 WG 接口端口冲突（`409`）。

### BGP 会话（BgpSession）

源码：`apps/control-server/app/api/v1/admin/bgp_sessions.py`。写端点接受完整 `BgpSessionSpec`（`spec` 字段），校验后物化广播。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/nodes/{node_id}/bgp-sessions` | 列出节点会话（按 `sort_order`, `id`） |
| `POST` | `/nodes/{node_id}/bgp-sessions` | 创建会话并物化广播，`201`；同节点重名 `409` |
| `GET` | `/bgp-sessions/{session_id}` | 取单会话 |
| `PATCH` | `/bgp-sessions/{session_id}` | 局部更新（`spec` / `sort_order` / `peering_id`），物化广播 |
| `DELETE` | `/bgp-sessions/{session_id}` | 删除并物化广播，`204` |

`SessionIn`：`spec`（`BgpSessionSpec` dump）、可选 `peering_id`、`sort_order`。`SessionPatch` 各字段可选 + `clear_peering`。`SessionOut`：`{ id, node_id, peering_id, name, remote_asn, enabled, sort_order, spec }`。

> 注意：iBGP / OSPF 内部互联**不是** `bgp_sessions` 记录，由 `bird.internal_topology` 自动合成，见路由组的 `internal-topology` 端点。

### DNS 组（DnsGroup / Zone / Record）

源码：`apps/control-server/app/api/v1/admin/dns_groups.py`。三级模型：`DnsGroup`（`name` + `bind_addresses`）→ `DnsGroupZone`（组声明的权威 zone + 可选 SOA）→ `DnsRecord`（扁平 `name`/`type`/`content`）。节点经 `Node.dns_group_id` 订阅；多节点订阅同组 = anycast。**组 / zone / 记录的写入会重新物化该组全部成员节点并广播**。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/dns-groups` | 列出 DNS 组 |
| `POST` | `/dns-groups` | 创建组，`201`；重名 `409` |
| `GET` | `/dns-groups/{group_id}` | 取单组（含 `zone_count` / `member_count`） |
| `PATCH` | `/dns-groups/{group_id}` | 更新组，重物化成员并广播 |
| `DELETE` | `/dns-groups/{group_id}` | 删除组（成员节点 `dns_group_id` 置 null），重物化广播，`204` |
| `GET` | `/dns-groups/{group_id}/zones` | 列出组的权威 zone |
| `POST` | `/dns-groups/{group_id}/zones` | 创建 zone，`201`；组内重名 `409` |
| `PATCH` | `/dns-groups/{group_id}/zones/{zone_id}` | 更新 zone |
| `DELETE` | `/dns-groups/{group_id}/zones/{zone_id}` | 删除 zone，`204` |
| `GET` | `/dns-groups/{group_id}/zones/{zone_id}/records` | 列出 zone 记录（按 `sort_order`, `id`） |
| `POST` | `/dns-groups/{group_id}/zones/{zone_id}/records` | 创建记录，`201` |
| `PATCH` | `/dns-groups/{group_id}/zones/{zone_id}/records/{record_id}` | 更新记录 |
| `DELETE` | `/dns-groups/{group_id}/zones/{zone_id}/records/{record_id}` | 删除记录，`204` |
| `PUT` | `/nodes/{node_id}/dns-group` | 给节点分配 / 取消 DNS 组（`dns_group_id` 可为 null），重物化广播 |

`DnsGroupIn`：`name`、`bind_addresses[]`、`cache_ttl_seconds`（默认 300）、`forwards[]`、`enabled`（默认 `true`）。`DnsZoneIn`：`zone`（合法域名，放行 RFC 2317 `allow_slash`）、可选 `primary_ns` / `admin_email` / `soa_*` / `default_ttl` / `enabled`。`DnsRecordIn`：`name`、`type`、`content`、可选 `ttl` / `comment` / `enabled` / `sort_order`。`PUT /nodes/{id}/dns-group` 请求 `{ "dns_group_id": <int|null> }`。

### Token（Agent Token）

源码：`apps/control-server/app/api/v1/admin/tokens.py`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/nodes/{node_id}/agent-tokens` | 列出该节点全部 token 元信息（不含 secret） |
| `POST` | `/nodes/{node_id}/agent-tokens` | 签发新 token，`201`，响应一次性返回 secret |
| `POST` | `/agent-tokens/{token_id}/rotate` | 轮换：撤旧签新，`201`，返回新 secret |
| `DELETE` | `/agent-tokens/{token_id}` | 撤销 token，`204` |

`AgentTokenIssueIn`（请求体可省略）：可选 `token`（自定义字面量，须 base64url + 最小熵，否则 `400`）/ `agent_id` / `ttl_seconds`（≥1）。`AgentTokenRotateIn`：可选 `ttl_seconds`。响应 `AgentTokenOut`：`{ token, secret?, node_id, agent_id, issued_at, expires_at?, revoked_at? }`——签发 / 轮换时 `token` 字段即完整 secret（唯一一次）。

```json
// POST /api/v1/admin/nodes/edge1/agent-tokens
{ "ttl_seconds": 2592000 }
```

```json
// 201 Created（token 字段即完整 secret，仅此一次）
{
  "token": "agt_ab12cd.s3cr3tVALUEonlyShownOnce",
  "secret": "agt_ab12cd.s3cr3tVALUEonlyShownOnce",
  "node_id": "edge1",
  "agent_id": "agent-edge1",
  "issued_at": "2026-06-22T00:00:00Z",
  "expires_at": "2026-07-22T00:00:00Z",
  "revoked_at": null
}
```

### Enrollment Token

源码：`apps/control-server/app/api/v1/admin/enrollment_tokens.py`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/enrollment-tokens` | 列出全部门票元信息（不含 secret） |
| `POST` | `/enrollment-tokens` | 创建门票，`201`，响应一次性返回 secret |
| `DELETE` | `/enrollment-tokens/{token_id}` | 删除门票，`204` |

`EnrollmentTokenIn`（可省略）：可选 `token`（字面量，须 base64url + 最小熵，已存在 `409`）/ `node_id`（绑定到该节点，未知 `400`）/ `description` / `expires_at`。响应 `EnrollmentTokenCreated`：`{ token_id, node_id?, description?, expires_at?, used_at?, created_at, secret }`（`secret` 仅创建可见）。

### 注册审批（Registrations）

源码：`apps/control-server/app/api/v1/admin/registrations.py`。审批只是「门禁名单」，approve 不自动 provision / 发 token——真正下发仍需 `POST /admin/provision` 或逐资源 CRUD。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/registrations?status=` | 列出注册请求；`status` ∈ `pending`/`approved`/`rejected`，缺省全部 |
| `POST` | `/registrations/{registration_id}/approve` | 标记 approved；未知 `404` |
| `POST` | `/registrations/{registration_id}/reject` | 标记 rejected；未知 `404` |

请求体 `RegistrationDecisionIn`（可省略）：`{ "note": "..." }`。`GET` 返回 `{ "registrations": [...] }`，审批端点返回该注册记录的 dict。

### Provision

源码：`apps/control-server/app/api/v1/admin/provision.py`。接受一份**完整 DesiredState**，一次性落库并发布为新一代；同 `node_id` 重复 provision 幂等覆盖（不报 `409`）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/provision` | 整节点 provision，`201`；DesiredState 校验失败 `422` |

`ProvisionIn`：`{ "state": <完整 DesiredState 的 JSON dump>, "agent_token": "<可选固定 token>" }`。`ProvisionOut`：`{ node_id, generation, subscribers, delivered }`。

```json
// POST /api/v1/admin/provision
{
  "state": {
    "node": { "node_id": "edge1", "asn": 4242420000, "router_id": "172.20.0.1" },
    "interfaces": [],
    "bgp_sessions": [],
    "bird": { "internal_topology": { "routers": [], "hosts": [] } }
  },
  "agent_token": "agt_fixed.bootstrapTokenForLabUse"
}
```

```json
// 201 Created
{ "node_id": "edge1", "generation": 1, "subscribers": 0, "delivered": 0 }
```

（`state` 的完整字段见 [DesiredState](../reference/desired-state.md)。）

### 健康（Health，只读）

源码：`apps/control-server/app/api/v1/admin/health.py`。数据来自 `NodeStatusStore`（agent 上报）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 全 fleet 健康概览（`{ summary, nodes }`） |
| `GET` | `/nodes/{node_id}/health` | 单节点健康 + 最近 snapshot/report/apply；无上报 `404` |
| `GET` | `/nodes/{node_id}/status-events?kind=&limit=` | 上报历史；`kind` ∈ `snapshot`/`report`/`apply`/`reresolve`，`limit` 1..500（默认 50） |

### 路由（Routing，只读）

源码：`apps/control-server/app/api/v1/admin/routing.py`。数据来自 `RoutingStore`（agent 周期上报的 `RoutingTableSnapshot` 聚合）。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/nodes/{node_id}/internal-topology` | iBGP + OSPF 内部互联视图（拓扑配置 + 路由层 liveness）；无 DesiredState `404` |
| `GET` | `/routing/fleet` | 全 fleet 路由概览 |
| `GET` | `/nodes/{node_id}/routing/summary` | 全表规模 + RPKI / 前缀长度 / AS path 分布；无上报 `404` |
| `GET` | `/nodes/{node_id}/routing/origins?limit=` | 起源 AS Top 榜（`limit` 1..1000，默认 50） |
| `GET` | `/nodes/{node_id}/routing/prefixes?family=&scope=&q=&limit=&offset=` | 分页 / 过滤路由检索 |
| `GET` | `/nodes/{node_id}/routing/timeline?limit=` | 路由表趋势 + churn（`limit` 1..500，默认 200） |

`prefixes` 查询参数：`family` ∈ `4`/`6`（缺省全部）；`scope` ∈ `all`/`local`/`external`（默认 `all`）；`q` 文本过滤（≤128）；`limit` 1..1000（默认 100）；`offset` ≥0（默认 0）。

### 审计（Audit，只读）

源码：`apps/control-server/app/api/v1/admin/audit.py`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/audit-log?limit=` | 按时间倒序返回最近 Admin 写操作（`limit` 1..1000，默认 100）；返回 `{ "entries": [...] }` |

---

## Agent HTTP API

源码：`apps/control-server/app/api/v1/agent_http.py`，前缀 `/api/v1/agent`。除 `/register` 外均需 Agent Bearer，且 payload `node_id` 必须等于 token 绑定节点（否则 `403`）。

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `POST` | `/register` | 体内 enrollment token | 注册换取 agent token |
| `GET` | `/desired-state` | Agent Bearer | 拉取本节点完整 DesiredState；无则 `404` |
| `GET` | `/recovery-public-key` | Agent Bearer | 取离线托管恢复公钥（PEM）+ 指纹 |
| `POST` | `/wireguard-keys` | Agent Bearer | 登记节点 WG 公钥 + 托管密文，一致性校验 + 对端传播 |
| `POST` | `/runtime-snapshot` | Agent Bearer | 上报运行时快照（容器 / 接口 / WG / BGP 观测） |
| `POST` | `/routing-table` | Agent Bearer | 上报 BIRD 路由全表观测 |
| `POST` | `/wireguard-reresolve` | Agent Bearer | 上报 WG endpoint 重解析结果（自愈观测） |
| `POST` | `/reconciliation-report` | Agent Bearer | 上报一次 reconcile 报告 |
| `POST` | `/apply-result` | Agent Bearer | 上报一次 apply 结果 |

### POST /agent/register

请求 `AgentRegistrationRequest`：`{ enrollment_token, requested_node_id, inventory }`（`inventory` 为 `HostInventory`：`hostname` / `os` / `arch` 等）。enrollment token 无效 / 绑定节点不符 `401`；节点被 admin reject `403`。响应 `AgentRegistrationResponse`：

- `ACCEPTED`：节点已 provision 且审批放行 → `node_id` / `agent_id` / `agent_token` / `desired_state_generation` 同时非空。
- `PENDING_APPROVAL`：节点未 provision / 审批 pending / 已建但尚无第一代 → `agent_token` 为 null，`message` 说明原因。

```json
// POST /api/v1/agent/register
{
  "enrollment_token": "ent_xxxx.oneTimeSecret",
  "requested_node_id": "edge1",
  "inventory": {
    "hostname": "edge1.example",
    "os": "linux",
    "arch": "x86_64",
    "has_systemd": true,
    "capabilities": []
  }
}
```

```json
// 200 OK (ACCEPTED)
{
  "status": "accepted",
  "node_id": "edge1",
  "agent_id": "agent-edge1",
  "agent_token": "agt_ab12cd.s3cr3tVALUEonlyShownOnce",
  "desired_state_generation": 3,
  "message": null
}
```

### POST /agent/wireguard-keys

请求 `WireGuardKeyReport`：`{ node_id, public_key, private_key_escrow? }`。公钥与已记录不符 → `409` 且事务回滚；未知节点 `404`。响应 `WireGuardKeyReportResult`：`{ node_id, accepted, status, detail?, propagated_to[] }`，`status` ∈ `stored`/`matched`/`rejected`。

### POST /agent/runtime-snapshot

请求 `RuntimeSnapshot`：`{ node_id, generation?, captured_at, containers[], interfaces[], wireguard_interfaces[], bgp_protocols[], ... }`。响应：

```json
// 200 OK
{ "accepted": true, "node_id": "edge1", "generation": 3, "containers": 4, "interfaces": 6 }
```

其余上报端点的响应均为确认型 dict：

- `/routing-table` → `{ accepted, node_id, observation, routes }`
- `/wireguard-reresolve` → `{ accepted, node_id, checked, reresolved }`
- `/reconciliation-report` → `{ accepted, node_id, status, drift_items }`
- `/apply-result` → `{ accepted, node_id, generation, status }`

`/recovery-public-key` 返回 `RecoveryPublicKeyResponse`：`{ configured, public_key_pem?, fingerprint? }`（未配置时 `configured=false`）。

---

## Agent WebSocket API

源码：`apps/control-server/app/api/v1/agent_ws.py`。

| 端点 | 说明 |
| --- | --- |
| `WS /api/v1/agent/ws/{node_id}` | 节点私有事件通道 |

**握手**：复用 HTTP 的 `Authorization: Bearer <agent token>` 头（不在 query 里传 token）。鉴权 / 通道隔离：

- token 解析失败 → 以关闭码 `4401` 关闭（未握手 accept）。
- token 绑定的 `node_id` 与 URL 路径里的 `{node_id}` 不一致 → 关闭码 `4403`。

**数据契约**：连接成功后服务端立即下发一条 `hello`，之后只下发「门铃事件」，agent 收到后回到 HTTP 拉取真实业务数据。服务端当前**不接受** agent 经 WS 推送业务数据（reader 仅用于感知断连）。

事件类型（`apps/control-server/app/schemas/events.py`）：

| `type` | 何时下发 | 字段 |
| --- | --- | --- |
| `hello` | 握手成功后立即 | `{ type, node_id, generation? }` |
| `desired_state_updated` | 控制面发布新世代 | `{ type, generation(≥1), reason? }` |
| `snapshot_request` | 要求 agent 主动上报快照 | `{ type, reason? }` |

```json
// 连接成功后第一条
{ "type": "hello", "node_id": "edge1", "generation": 3 }
```

```json
// 配置变更门铃
{ "type": "desired_state_updated", "generation": 4, "reason": "interface updated" }
```

---

## 健康探针

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| `GET` | `/healthz` | 无 | 存活 + DB 连通性探针（`apps/control-server/app/main.py:117`）；DB 不可达返回 `503` `{ "status": "unavailable", "database": "down" }`，正常 `{ "status": "ok", "database": "up" }` |
| `GET` | `/api/v1/healthz` | 无 | 轻量存活探针（`apps/control-server/app/api/v1/health.py`）；恒返回 `{ "status": "ok" }` |
