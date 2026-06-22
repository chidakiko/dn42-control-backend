# 安全模型

本文讲系统的安全姿态：鉴权与 token、注册审批闸门、被刻意禁止的控制模型、私钥处理、写盘与容器边界。配置项见 [../reference/configuration.md](../reference/configuration.md)，接口鉴权见 [../reference/api.md](../reference/api.md)。

## 鉴权边界

| 接口面 | 鉴权 | 说明 |
| --- | --- | --- |
| Admin API（`/api/v1/admin/*`） | `Authorization: Bearer <DN42_CONTROL_ADMIN_TOKEN>` | **fail-closed**：未配置 admin token 时全部 403；token 错 401 |
| Agent HTTP（`/api/v1/agent/*`，除 register） | `Authorization: Bearer <agent token>` | token 绑定 `node_id` |
| Agent WebSocket（`/agent/ws/{node_id}`） | Bearer，且 `principal.node_id == URL node_id` | 不符 4403、无效 4401 |
| `POST /agent/register` | 请求体内 enrollment token | 唯一不需 Bearer 的写接口 |
| `GET /healthz` | 无 | DB 探针 |

所有 admin 写操作（含鉴权失败）由中间件记入 `admin_audit_log`，见 [../reference/database.md](../reference/database.md#审计)。

## token 哈希模型

- **数据库只存 sha256 哈希**，不存明文。
- **Agent token**：格式 `<id>.<secret>`（`id` 为非密查找键）或固定字面量（`literal_token_id` 从哈希派生 id）。明文只在签发响应里出现一次，不落日志、不入库。`resolve()` 校验未撤销、未过期。`rotate()` 撤旧签新。
- **Enrollment token**：同哈希模型。`node_id` 为空 = 任意节点可用，非空 = 仅该节点；一次性（`used_at` 标记消费）。

## 注册审批闸门

未知节点无法自助上线，必须经管理员放行（`agent_http.py` register + `PendingRegistrationStore`）：

1. agent `POST /agent/register`（enrollment token + requested_node_id + inventory）。
2. 校验 enrollment token（全局或绑定、未消费、未过期）。
3. 查审批状态：
   - `rejected` → **403**（显式否决）。
   - `pending` → 返回 `PENDING_APPROVAL`，**不消费 enrollment token**，agent 周期重试。
   - 未知节点 → 记为 pending 注册申请。
   - 已落库且有 generation → 签发 agent token，标记 enrollment 已用。

"approve 只是放行名单，provision 才下发 `DesiredState`"——两步分离避免误放行即生效。流程见 [../guides/node-onboarding.md](../guides/node-onboarding.md)。

## 私钥处理（WireGuard escrow）

- 节点 WireGuard 私钥**在节点本地生成**，0600 存于状态目录，**永不写进渲染产物**（占位 `secret://`），永不上传明文。
- 公钥上报控制面，作为内部对端公钥派生的单一真源。
- 私钥用控制面下发的**离线恢复公钥** RSA-OAEP 封存（escrow）后上报，供灾难恢复。恢复私钥永不在控制面/节点出现，只在离线 `dn42-recover` 工具里使用。
- 控制面对上报公钥做一致性严格校验：不符返回 409 中止 apply。

完整 escrow 与恢复演练见 [../guides/secret-recovery.md](../guides/secret-recovery.md)。

## 禁止的控制模型

系统**刻意不提供**以下能力，这是核心安全设计而非缺失：

- ❌ 远程 shell / 任意命令执行接口。
- ❌ 控制面直接 push 命令到节点执行。
- ❌ 节点接收并执行控制面下发的脚本片段。

控制面能表达的只有**声明式期望状态**（`DesiredState`）。怎么达成完全由节点本地 Agent 基于本机观测决定（见 [architecture.md](architecture.md#最小扰动设计)）。即使控制面被攻陷，攻击者也只能改"想要什么"，无法直接在节点上执行任意命令——节点渲染的是受 schema 约束、经模板生成的配置，不是可执行指令流。

## 写盘与容器边界

- Agent 渲染文件路径强校验（禁 `..`、绝对路径、NUL、Windows 盘符，见 `dn42_runtime`），原子写盘。
- 容器由受管 label（`dn42.managed` 等）标识；Agent 只动带本节点 label 的容器，不碰其它容器。
- systemd 单元用 `ProtectSystem=strict`、`NoNewPrivileges=true`、受限 `ReadWritePaths`（见 [../guides/deployment.md](../guides/deployment.md)）。

## 多管理员 / RBAC

当前为**单一 admin token**模型，无多管理员、无细粒度 RBAC（见 [../ROADMAP.md](../ROADMAP.md)）。生产应妥善保管 admin token、用 TLS 终止反代、按需收紧 CORS 白名单。
