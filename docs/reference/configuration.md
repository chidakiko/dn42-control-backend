# 配置参考（Configuration Reference）

本文是 DN42 控制面后端**两个程序**——Control Server（控制服务器）与 Node Agent（节点代理）——全部配置项的**唯一真相源（single source of truth）**。所有条目均逐字对照源码列出，未在代码中出现的配置不会记录于此。

- Control Server 配置类：`apps/control-server/app/core/config.py`（`ControlServerConfig` / `from_env()`）。
- Node Agent 配置类：`apps/node-agent/agent/core/config.py`（`AgentConfig` / `load_agent_config()`）与 CLI `apps/node-agent/agent/main.py`（`_build_parser()`）。

---

## 1. Control Server

Control Server 是一个 dataclass `ControlServerConfig`（`apps/control-server/app/core/config.py:71`），所有字段都有内置默认值；`from_env()`（同文件 `:112`）按下表的环境变量逐项覆盖，未设置的项保留默认。所有环境变量统一以 `DN42_CONTROL_` 为前缀。

| 环境变量 | 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `DN42_CONTROL_DATABASE_URL` | `database_url` | str (SQLAlchemy 异步 DSN) | `sqlite+aiosqlite:///<仓库根>/control.db` | SQLAlchemy 异步 DSN。默认锚定在**仓库根目录**下的 `control.db`（`config.py:16-17`，避免随 cwd 漂移），适合本地与 CI。生产改用 Postgres（`postgresql+asyncpg://user:pass@host:5432/db`）或 MySQL（`mysql+asyncmy://...`）。同一变量也被 Alembic 迁移读取（见 [§1.3](#13-数据库-url-与迁移)）。详见 [部署指南](../guides/deployment.md)。 |
| `DN42_CONTROL_ENROLLMENT_TOKEN` | `enrollment_token` | str \| None | `"enroll-token"` | 全局 bootstrap 注册 token；agent `register` 时与 `enrollment_tokens` 表中按节点签发的 token **并行接受**。**显式设为空字符串**即关闭全局 token（`from_env` 把 `""` 归一为 `None`，`config.py:117-121`），此后只允许表内按节点 token 注册。变量完全不设置时保留默认值 `"enroll-token"`。 |
| `DN42_CONTROL_ADMIN_TOKEN` | `admin_token` | str \| None | `None` | Admin API 的 Bearer token。**fail-closed**：值为 `None`（变量未设置或为空字符串，`config.py:126`）时 Admin API 整体拒绝（一律 403）。生产**必须显式配置**。 |
| `DN42_CONTROL_SEED_BOOTSTRAP_NODE` | `seed_bootstrap_node` | bool | `False` | 是否在 DB 为空时播种内置 demo 节点。布尔解析见 `_env_flag`（`config.py:20-26`）：`1/true/yes/on`（大小写不敏感）为真，其余为假。默认 `False` 表示启动即空库，节点数据应由导入 / provision 流程写入。 |
| `DN42_CONTROL_BOOTSTRAP_NODE_ID` | `bootstrap_node_id` | str | `"edge1"` | 内置 demo 节点的 `node_id`，仅在 `seed_bootstrap_node` 开启时使用。 |
| `DN42_CONTROL_BOOTSTRAP_AGENT_TOKEN` | `bootstrap_agent_token` | str | `"mvp-agent-token"` | 与 bootstrap 节点关联的初始 Bearer token，便于本地联调。仅在 `seed_bootstrap_node` 开启时有意义。 |
| `DN42_CONTROL_RECOVERY_PUBLIC_KEY` | `recovery_public_key_pem` | str \| None | `None` | 离线托管「恢复公钥」。取值既可是**内联 PEM**（以 `-----BEGIN` 开头），也可是**PEM 文件路径**（`_load_recovery_public_key`，`config.py:52-68`）。节点用它封装 WG 私钥后上报；控制面只存密文、**永不持有恢复私钥**。`None`（未设置）表示未启用托管——节点仍上报公钥做一致性校验，但不产生托管密文。**注意**：若给的是文件路径且文件不存在，启动期会抛 `FileNotFoundError`（fail-fast，`config.py:64-67`）。 |
| `DN42_CONTROL_CORS_ORIGINS` | `cors_origins` | tuple[str, …]（逗号分隔） | `("http://localhost:5173", "http://127.0.0.1:5173")` | 浏览器管理面（apps/web）跨源直连本服务的 CORS 白名单。解析见 `_parse_cors_origins`（`config.py:41-49`）：逗号分隔、各项去空白、丢弃空项。**未设置**时返回默认（放行本地 Vite dev server）；**显式设为空字符串**表示关闭跨源（白名单为空元组）。生产填管理面真实源（或 `*`）。 |
| `DN42_CONTROL_HEALTH_STALE_AFTER` | `health_stale_after_seconds` | float（秒） | `900.0` | 健康判定失联阈值：在此时长内未上报的 `ok` 节点降为 `stale`。浮点解析见 `_env_float`（`config.py:29-38`）：缺失、空白或非法值回退默认。 |
| `DN42_CONTROL_HEALTH_DOWN_AFTER` | `health_down_after_seconds` | float（秒） | `3600.0` | 健康判定宕机阈值：超过此时长完全无上报判为 `down`，覆盖任何已知状态。解析同 `_env_float`。 |
| `DN42_CONTROL_REDIS_URL` | `redis_url` | str \| None | `None` | Redis 缓存 DSN（如 `redis://redis:6379/0`）。缓存 desired-state（generation 键）/ 节点健康（10s）/ 路由聚合（30s）等高频读，写时主动失效。**`None`（未设置）即不启用缓存**——所有读直接走 DB（缓存层全程 no-op）；Redis 不可用时同样自动回落 DB，**缓存是旁路、不影响正确性**。docker 全栈方案内置 redis 并已注入此变量。 |

### 1.1 解析助手（parsing helpers）

| 助手 | 位置 | 行为 |
| --- | --- | --- |
| `_env_flag(name, default)` | `config.py:20` | 布尔：未设置返回 `default`；否则 `1/true/yes/on`（去空白、小写）为真。 |
| `_env_float(name, default)` | `config.py:29` | 浮点：未设置 / 纯空白 / 解析失败 → `default`。 |
| `_parse_cors_origins(raw, default)` | `config.py:41` | `None` → `default`；否则逗号分隔、去空白、丢空项；`""` → 空元组（关闭跨源）。 |
| `_load_recovery_public_key(raw)` | `config.py:52` | 空 → `None`；以 `-----BEGIN` 开头 → 视为内联 PEM 原样返回；否则当作文件路径读取（ASCII），不存在则 `FileNotFoundError`。 |

### 1.2 fail-closed / 空字符串语义速查

| 变量 | 「未设置」 | 「设为空字符串 `""`」 |
| --- | --- | --- |
| `DN42_CONTROL_ENROLLMENT_TOKEN` | 保留默认 `"enroll-token"` | `None` → 关闭全局 token |
| `DN42_CONTROL_ADMIN_TOKEN` | `None` → Admin API 全 403 | `None` → Admin API 全 403 |
| `DN42_CONTROL_CORS_ORIGINS` | 默认本地白名单 | 空元组 → 关闭跨源 |
| `DN42_CONTROL_RECOVERY_PUBLIC_KEY` | `None` → 不托管 | `None` → 不托管 |

### 1.3 数据库 URL 与迁移

Alembic 迁移与运行时**共享同一变量** `DN42_CONTROL_DATABASE_URL`：

- `migrations/env.py:36-51`（`_resolve_url()`）优先读该环境变量，缺失时退回 `alembic.ini` 的 `sqlalchemy.url`（默认 `sqlite+aiosqlite:///./control.db`，仅为 `alembic --help` 的无害占位）。
- 迁移期把异步驱动替换为同步驱动：`+aiosqlite` → 去除、`+asyncpg` → `+psycopg2`、`+asyncmy` → `+pymysql`（`env.py:43-47`）。
- 从仓库根运行：`alembic upgrade head`。Postgres / MySQL 生产部署的完整步骤见 [部署指南](../guides/deployment.md)。

---

## 2. Node Agent

### 2.1 三来源与优先级

Agent 配置有**三个来源**，优先级从高到低（`apps/node-agent/agent/core/config.py:4-6`）：

```
CLI 参数  >  环境变量 (DN42_AGENT_*)  >  TOML 文件  >  内置默认值
```

加载流程：

1. `load_agent_config(toml_path)`（`config.py:67`）：先 `AgentConfig()` 取默认，若 TOML 文件存在则 `_apply_toml` 覆盖（`config.py:107`），再 `_apply_env` 用环境变量覆盖（`config.py:145`），最后 `_validate_choices` 校验枚举。
2. CLI 层 `_config_from_args`（`main.py:100`）在上一步结果之上叠加命令行覆盖（仅覆盖非 `None` 的项）。

补充约定：

- **TOML 文件不存在不报错**，相当于跳过该来源（`config.py:75`）。文件路径由 `--config` 指定；约定默认 `/etc/dn42-control/agent.toml`。
- TOML 顶层可用 `[agent]` 表，也可直接平铺（`agent_section = payload.get("agent", payload)`，`config.py:111`）。
- TOML **只识别白名单字段**（`_ALLOWED_KEYS`，`config.py:89-104`），出现未知键会抛 `ConfigError`，避免静默拼写错误。
- `with_overrides` 仅应用**非 None** 的覆盖项（`config.py:60-64`），所以低优先级来源不会被高优先级来源的「未提供」清空。

### 2.2 CLI 参数

由 `_build_parser()` 定义（`apps/node-agent/agent/main.py:56`）。Agent **默认作为后台常驻守护进程**运行（启动即 reconcile + 连接控制面 WebSocket，收到事件再 reconcile）。

| 参数 | 含义 | 默认 |
| --- | --- | --- |
| `--config PATH` | `agent.toml` 配置文件路径（决定 TOML 来源）。 | 无（不读 TOML） |
| `--controller-url URL` | Control Server 基础 URL（覆盖 `controller_url`）。 | 见 `AgentConfig` |
| `--enrollment-token TOKEN` | 一次性 enrollment token。 | 见 `AgentConfig` |
| `--requested-node-id ID` | 希望绑定的 `node_id`。 | 见 `AgentConfig` |
| `--hostname NAME` | 覆盖 inventory 中的 hostname。 | 见 `AgentConfig` |
| `--state-dir PATH` | 本地状态目录。 | `/var/lib/dn42-control` |
| `--rendered-dir PATH` | 渲染输出目录（覆盖默认派生路径）。 | 见 `AgentConfig` |
| `--desired-state PATH` | 离线运行使用的 desired-state JSON（覆盖 `desired_state_path`）。 | 无 |
| `--mode {apply,write-rendered,plan-only}` | reconcile 深度：`apply`（默认，写盘+部署+收敛）、`write-rendered`（只写渲染文件、不碰容器）、`plan-only`（只规划）。 | `apply` |
| `--once` | **诊断**：只跑一次 reconcile 后退出，不进常驻循环。 | 关 |
| `--plan-only` | **诊断**：等价于 `--once --mode plan-only`。 | 关 |
| `--doctor` | **诊断**：跑一次自检（配置 / 状态目录 / 身份 / 控制面 / Docker / 指标）后退出。 | 关 |
| `--log-level LEVEL` | 日志级别，如 `INFO` / `DEBUG`。 | `INFO` |

#### 互斥与运行模式约束

- **诊断三选一**：`--once` / `--plan-only` / `--doctor` 同属一个 `add_mutually_exclusive_group()`（`main.py:80`），最多用其一。
- **`--plan-only` 与 `--mode` 冲突**：若同时给 `--plan-only` 和一个非 `plan-only` 的 `--mode`，抛 `SystemExit("--plan-only 与 --mode 冲突，请只用其一")`（`main.py:119-122`）。
- **`--controller-url` 与 `--desired-state` 互斥（XOR）**：两者最终值都非 `None` 时抛 `SystemExit("--controller-url 与 --desired-state 互斥")`（`main.py:128-132`）。在线（连控制面）与离线（吃本地 JSON）二选一。
- **常驻模式前置条件**（既非 `--doctor` 也非 `--once`/`--plan-only` 时，`main.py:157-164`）：
  - `controller_url` 必须存在，否则退出并提示用 `--once`/`--plan-only` 离线排障。
  - `mode` 不能是 `plan-only`（仅供单次诊断），否则退出。
- 运行分发（`main.py:144-170`）：`--doctor` → 打印自检 JSON，`ok` 则退出码 0 否则 1；`--once`/`--plan-only` → 跑一次并打印 summary，部署失败退出码 1；否则进入 `run_watch` 常驻循环（`SIGTERM`/`SIGINT` 优雅退出）。

### 2.3 环境变量

由 `_ENV_KEYS` 映射（`config.py:128-142`），统一以 `DN42_AGENT_` 为前缀。下表「字段」对应 `AgentConfig`（`config.py:28`），默认值即字段默认。

| 环境变量 | 字段 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `DN42_AGENT_CONTROLLER_URL` | `controller_url` | str \| None | `None` | Control Server 基础 URL。常驻模式必需。 |
| `DN42_AGENT_ENROLLMENT_TOKEN` | `enrollment_token` | str \| None | `None` | 一次性 enrollment token。 |
| `DN42_AGENT_REQUESTED_NODE_ID` | `requested_node_id` | str \| None | `None` | 希望绑定的 `node_id`。 |
| `DN42_AGENT_HOSTNAME` | `hostname` | str \| None | `None` | 覆盖 inventory hostname。 |
| `DN42_AGENT_STATE_DIR` | `state_dir` | Path | `/var/lib/dn42-control` | 本地状态目录（解析为 `Path`，`config.py:151-152`）。 |
| `DN42_AGENT_RENDERED_DIR` | `rendered_dir` | Path \| None | `None`（派生） | 渲染输出目录（解析为 `Path`）。留空则从节点目录派生（见 [§2.4](#24-状态目录布局)）。 |
| `DN42_AGENT_MODE` | `mode` | `apply`/`write-rendered`/`plan-only` | `apply` | reconcile 深度；非法值在 `_validate_choices` 抛 `ConfigError`（`config.py:82-86`）。 |
| `DN42_AGENT_LOG_LEVEL` | `log_level` | str | `INFO` | 日志级别。 |
| `DN42_AGENT_HTTP_TIMEOUT_SECONDS` | `http_timeout_seconds` | float | `10.0` | HTTP 请求超时（秒）。解析为 `float`，非数字抛 `ConfigError`（`config.py:158-161`）。 |
| `DN42_AGENT_LOCAL_CONVERGENCE` | `local_convergence` | bool | `True` | 是否执行本机收敛。`1/true/yes/on`（去空白、小写）为真（`config.py:162-163`）。 |
| `DN42_AGENT_ROUTING_INTERVAL_SECONDS` | `routing_interval_seconds` | float | `300.0` | 路由全表周期采集间隔（秒），独立于 reconcile 的纯观测。**设 0 关闭**采集。 |
| `DN42_AGENT_RERESOLVE_INTERVAL_SECONDS` | `reresolve_interval_seconds` | float | `45.0` | WG endpoint 周期重解析间隔（秒），自愈对端动态 DNS 漂移后内核钉死旧 IP。**设 0 关闭**。 |
| `DN42_AGENT_BIRD_SOCKET_PATH` | `bird_socket_path` | str \| None | `None`（派生） | BIRD 控制 socket 路径的**显式覆盖**。留空则从渲染目录推导 `<rendered_dir>/run/bird/bird.ctl`；仅非常规部署 / 联调需要指向别处时设置。 |

> 说明：CLI 没有暴露 `http_timeout_seconds` / `local_convergence` / `routing_interval_seconds` / `reresolve_interval_seconds` / `bird_socket_path` 这几项——它们只能通过环境变量或 TOML 设置。

### 2.4 状态目录布局

由 `AgentPaths`（`apps/node-agent/agent/core/paths.py`）定义，所有节点级文件落在 `<state_dir>/nodes/<node_id>/` 之下：

| 路径 | 内容 |
| --- | --- |
| `nodes/<node_id>/identity.json` | 持久化 agent 身份与世代信息。 |
| `nodes/<node_id>/desired-state.json` | 最近一次成功 Desired State 的本地副本。 |
| `nodes/<node_id>/rendered/` | 渲染输出（配置文件与镜像构建上下文根；`rendered_dir` 留空时即此处）。 |
| `nodes/<node_id>/snapshots/` | RuntimeSnapshot 与 ReconciliationReport 历史归档。 |
| `nodes/<node_id>/metrics.json` | reconcile 运行指标（次数 / 失败 / 时长 / 最近状态）。 |
| `nodes/<node_id>/containers/` | 已应用容器定义记录（字段级 diff reason 数据源）。 |
| `nodes/<node_id>/secrets/` | 节点本地密钥目录（`0700`），含 `secrets/wireguard/node.key`（`0600`，一节点一把 WG 私钥）。**不进渲染产物、不上报、不入 file plan**。 |

Agent 内部机制与完整 CLI 见 [Node Agent 内部说明](../internals/node-agent.md) 与 [CLI 与脚本参考](../reference/cli-and-scripts.md)。

---

## 3. 示例

### 3.1 Control Server 最小 `.env`

```dotenv
# 异步 SQLAlchemy DSN；生产建议 Postgres，见 ../guides/deployment.md
DN42_CONTROL_DATABASE_URL=postgresql+asyncpg://dn42:secret@127.0.0.1:5432/dn42_control

# 全局 enrollment token（agent 首次注册用）；设为空字符串可只走 per-node token
DN42_CONTROL_ENROLLMENT_TOKEN=change-me

# Admin API Bearer——不设置则 /admin 一律 403（fail-closed），生产必填
DN42_CONTROL_ADMIN_TOKEN=change-me-admin

# 可选：浏览器管理面跨源白名单（逗号分隔）
DN42_CONTROL_CORS_ORIGINS=https://panel.example.dn42

# 可选：离线恢复公钥（内联 PEM 或文件路径），启用 WG 私钥托管
# DN42_CONTROL_RECOVERY_PUBLIC_KEY=/etc/dn42-control/recovery.pub.pem
```

跑迁移（与运行时共享同一 DSN）：

```bash
DN42_CONTROL_DATABASE_URL=postgresql+asyncpg://dn42:secret@127.0.0.1:5432/dn42_control \
  alembic upgrade head
```

### 3.2 Node Agent 最小 `agent.toml`

```toml
[agent]
controller_url = "https://control.example.dn42"
enrollment_token = "change-me"
requested_node_id = "edge1"
state_dir = "/var/lib/dn42-control"
log_level = "INFO"
# 可选调参
# http_timeout_seconds = 10.0
# routing_interval_seconds = 300.0   # 0 关闭路由采集
# reresolve_interval_seconds = 45.0  # 0 关闭 WG endpoint 重解析
```

### 3.3 Node Agent systemd env-file（生产推荐）

模板单元 `deploy/systemd/dn42-node-agent@.service` 用 `%i` 作为 `node_id`，并读取 `/etc/dn42-control/%i.env`。节点专属 env 文件示例：

```dotenv
# /etc/dn42-control/edge1.env
DN42_AGENT_CONTROLLER_URL=https://control.example.dn42
DN42_AGENT_ENROLLMENT_TOKEN=change-me
# STATE_DIR 与 REQUESTED_NODE_ID 已由 unit 注入（=/var/lib/dn42-control、=%i）
```

启用：`sudo systemctl enable --now dn42-node-agent@edge1`。

完整生产部署（venv、PYTHONPATH、Postgres、加固选项）见 [部署指南](../guides/deployment.md)。
