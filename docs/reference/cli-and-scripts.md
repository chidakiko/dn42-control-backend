# CLI 与脚本参考

本文是 Node Agent 命令行、以及仓库里所有运维 / 开发脚本的参考清单。配置项（env / TOML）见 [configuration.md](configuration.md)；具体操作流程见 [../guides/](../guides/)。

## Node Agent CLI

入口：`python -m agent.main`（安装后亦可用 `dn42-node-agent`）。参数解析见 [apps/node-agent/agent/main.py:56](../../apps/node-agent/agent/main.py)。

**默认行为是后台常驻守护进程**：启动即 reconcile 一次，然后连接控制面 WebSocket，收到事件再 reconcile。`--once` / `--plan-only` / `--doctor` 是诊断用的单次模式。

| 参数 | 含义 | 默认 |
| --- | --- | --- |
| `--config PATH` | `agent.toml` 配置文件路径 | 内置默认 / `/etc/dn42-control/agent.toml` |
| `--controller-url URL` | Control Server 基础 URL（常驻模式必需） | 无 |
| `--enrollment-token TOKEN` | 一次性 enrollment token | 无 |
| `--requested-node-id ID` | 希望绑定的 `node_id` | 无（从状态目录推断） |
| `--hostname FQDN` | 覆盖 inventory 中的 hostname | 自动探测 |
| `--state-dir PATH` | 本地状态目录 | `/var/lib/dn42-control` |
| `--rendered-dir PATH` | 渲染输出目录（覆盖默认） | `<state-dir>/nodes/<node_id>/rendered` |
| `--desired-state PATH` | 离线运行使用的 DesiredState JSON | 无 |
| `--mode {apply,write-rendered,plan-only}` | reconcile 深度 | `apply` |
| `--once` | 诊断：只跑一次 reconcile 后退出 | 关 |
| `--plan-only` | 诊断：等价于 `--once --mode plan-only` | 关 |
| `--doctor` | 诊断：跑一次自检后退出 | 关 |
| `--log-level LEVEL` | 日志级别（`INFO` / `DEBUG` …） | `INFO` |

### 模式与约束

- **`apply`**（默认）：写盘 + 部署容器 + 本机收敛（WG / BIRD 热重载）。
- **`write-rendered`**：只写渲染文件，不碰容器（无 Docker 的演示 / 调试环境）。
- **`plan-only`**：只渲染 + 规划，不写不部署。
- 互斥：`--once` / `--plan-only` / `--doctor` 三选一（同一个互斥组）。
- 冲突：`--plan-only` 与 `--mode`（非 `plan-only`）冲突；`--controller-url` 与 `--desired-state` 互斥。
- 常驻模式前置：必须有 `--controller-url`，且 `mode` 不能是 `plan-only`。

详细运行模式与守护循环见 [../internals/node-agent.md](../internals/node-agent.md)。

### 退出码

| 模式 | 退出码 |
| --- | --- |
| `--doctor` | 自检全过 `0`，否则 `1` |
| `--once` / `--plan-only` | deploy 失败 `1`，否则 `0`（输出 JSON 摘要到 stdout） |
| 常驻 | 收到信号优雅退出 `0` |

---

## 运维脚本（`deploy/`）

这些是面向**生产 fleet** 的脚本，多数通过 Admin API（环境变量 `DN42_CP` = 控制面 URL、`DN42_ADMIN_TOKEN` = 管理 token）操作，默认 dry-run，需显式 `apply` / `deploy` 才落地，并写 `.json` 备份供回滚。

### 当前升级机制

| 脚本 | 作用 |
| --- | --- |
| `build_wheels.sh` | 构建 5 个一方 wheel（`dn42-{common,schemas,runtime,templates}` + node-agent），版本 = `1.0.<git-rev-count>`，产物入 `dist/` |
| `agent_pip_rollout.sh` | SSH 到节点，scp `dist/*.whl` → `/opt/dn42-wheels`，离线 `pip install -U --no-index --find-links`，重启 `dn42-node-agent.service` |

升级流程详见 [../guides/upgrades-and-migrations.md](../guides/upgrades-and-migrations.md)。

### 一次性迁移 / 编排脚本（Python）

| 脚本 | 作用 |
| --- | --- |
| `renumber_loopbacks.py` | 把节点 loopback 对齐到单播 `/27`；同步所有节点 `internal_topology.hosts` + DNS 记录。默认 dry-run，`apply` 落地，`--verify` 校验 |
| `unify_internal_topology.py` | 把各节点 `internal_topology` 统一为全节点 full-mesh（修 pvg2 缺路由）。`deploy` / `--verify` / `--rollback`。⚠️ 历史版本硬编码旧地址，renumber 后须先更新再跑 |
| `dns_anycast_lo_cleanup.py` | 把 anycast DNS 地址从 `dn42-lo`（每节点）迁到 `dns-anycast` dummy（共享），剥重复。`--dry-run` / apply / `--rollback` |
| `bird_socket_mount_rollout.py` | 预注入 `/run/bird` 可写挂载（路由采集从 `docker exec birdc` 切到直连 socket）。`deploy` / `--verify` / `--rollback` |

### 历史一次性 rollout（已被 wheel 升级取代，留档）

`agent_arc11_full_rollout.sh`、`agent_drop_unknown_rollout.sh`、`agent_filtered_fulllist_rollout.sh`、`agent_filtered_routes_rollout.sh`、`agent_import_limit_rollout.sh`、`agent_invalid_routes_rollout.sh`、`agent_prefilter_rollout.sh`、`agent_reject_reason_rollout.sh`——都是 wheel 发布机制成型前，针对单次能力上线的手工滚动脚本。**新升级一律走 `build_wheels.sh` + `agent_pip_rollout.sh`**，这些仅作历史参考。

---

## 开发脚本（`scripts/`）

### `scripts/dev/` —— 本地演示 / 渲染

| 脚本 | 作用 |
| --- | --- |
| `provision-three-node.py` | 等控制面 `/healthz` 就绪后，向 `POST /api/v1/admin/provision` 灌入 3 个节点的本地 lab（独立脚本，对任意运行中的控制面用，如 docker 全栈方案起的控制面） |
| `render-local-three-node.py` | 把三节点 lab 渲染到磁盘（独立渲染，不依赖控制面） |
| `render-local-two-node.py` | 两节点 iBGP 拓扑本地渲染 |
| `render-two-internal-one-ebgp-demo.py` | 2 内部 + 1 外部 eBGP 对端的多场景渲染 |
| `sanitize_examples.py` | 清洗示例渲染产物（脱敏） |

### `scripts/tools/` —— 数据导入 / backfill

| 脚本 | 作用 |
| --- | --- |
| `import_node_config.py` | 解析既有节点配置（`bird.conf`、`wireguard/*.conf`、`scripts/wg/apply-*.sh`）为 `DesiredState`，导入 DB 或 `--dry-run` 输出 JSON |
| `backfill_node_lla.py` | 从 WG 接口 `addresses` 剥掉本端 `fe80::X/64` 副本（LLA 集中到 `NodeSpec.link_local` 后）。dry-run / apply / 幂等。见 [addressing-model.md](addressing-model.md#31-已解决的副本节点-lla-fe80x已收敛为派生) |
| `create_rdns_26.py` | 为 `/26` 寻址创建反向 DNS zone + 记录 |

### `scripts/db/`

预留目录（DB 管理工具）。Alembic 迁移见 [database.md](database.md#迁移alembic) 与 [../guides/upgrades-and-migrations.md](../guides/upgrades-and-migrations.md)。

---

## 离线恢复工具（`tools/dn42-recover/`）

`dn42_recover.py` —— 离线 WireGuard 私钥 escrow 恢复 CLI（只依赖 `dn42_common.crypto`，**永不在控制面运行**）：

| 子命令 | 作用 |
| --- | --- |
| `keygen` | 生成 RSA 恢复密钥对（私钥用口令加密 → `recovery-private.pem`，公钥 → `recovery-public.pem`） |
| `recover` | 用恢复私钥解封 `wg_interfaces` / 上报里的 escrow 密文，还原节点 WG 私钥；可 `--expect-public` 校验 |

完整 escrow 模型与恢复演练见 [../guides/secret-recovery.md](../guides/secret-recovery.md)。
