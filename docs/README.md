# 文档中心

`dn42-control-backend` 的全部文档都在这里。文档按**读者要做什么**分四层，遵循 [Diátaxis](https://diataxis.fr/) 框架：

| 层 | 目录 | 回答的问题 | 形态 |
| --- | --- | --- | --- |
| 教程 | [`tutorials/`](tutorials/) | 我第一次接触，怎么从零跑起来？ | 手把手、可照做 |
| 操作手册 | [`guides/`](guides/) | 我要完成某个具体任务，步骤是什么？ | 任务导向、面向目标 |
| 参考 | [`reference/`](reference/) | 这个接口/配置/字段/表到底是什么？ | 查得到、求精确 |
| 内部原理 | [`internals/`](internals/) | 系统为什么这样设计、怎么运转？ | 解释、配图 |

## 按场景选读

| 场景 | 从这里开始 |
| --- | --- |
| 完全没接触过本项目 | [overview.md](overview.md) → [tutorials/01-quickstart.md](tutorials/01-quickstart.md) |
| 要把系统部署到生产 | [guides/deployment.md](guides/deployment.md) |
| 要接入一个新节点 | [guides/node-onboarding.md](guides/node-onboarding.md) |
| 要建立 eBGP / iBGP 互联 | [guides/peering.md](guides/peering.md) |
| 要配 DNS / 任播 | [guides/dns-and-anycast.md](guides/dns-and-anycast.md) |
| 要 renumber 地址 / 看寻址分配 | [guides/addressing-and-renumber.md](guides/addressing-and-renumber.md) |
| 要升级 agent / 跑数据库迁移 | [guides/upgrades-and-migrations.md](guides/upgrades-and-migrations.md) |
| 节点不健康 / iBGP 缺路由 | [guides/monitoring-and-troubleshooting.md](guides/monitoring-and-troubleshooting.md) |
| 要用 Web 管理界面 | [guides/web-ui.md](guides/web-ui.md) |
| 要调用 / 对接 API | [reference/api.md](reference/api.md) |
| 要查某个配置项 / 环境变量 | [reference/configuration.md](reference/configuration.md) |
| 要查 `DesiredState` 字段 | [reference/desired-state.md](reference/desired-state.md) |
| 要查数据库表 | [reference/database.md](reference/database.md) |
| 想理解系统怎么运转 | [internals/architecture.md](internals/architecture.md) |
| 要改代码 / 写测试 | [contributing.md](contributing.md) |

## 全部文档

### 概览

| 文档 | 内容 |
| --- | --- |
| [overview.md](overview.md) | 系统是什么、解决什么问题、核心概念词汇表、一句话闭环 |

### 教程（tutorials/）

| 文档 | 内容 |
| --- | --- |
| [tutorials/01-quickstart.md](tutorials/01-quickstart.md) | 本地从零跑通：装依赖 → 起 Control Server → 起 Node Agent（plan-only / once / daemon）→ 打开 Web UI |

### 操作手册（guides/）

| 文档 | 内容 |
| --- | --- |
| [guides/deployment.md](guides/deployment.md) | 生产部署：systemd 两个单元、Postgres、`alembic upgrade head`、Web 静态托管 + CORS、recovery key 注入 |
| [guides/node-onboarding.md](guides/node-onboarding.md) | 节点接入全流程：enrollment token → register → 审批闸门 → provision → agent token 生命周期 |
| [guides/peering.md](guides/peering.md) | 建立 eBGP / iBGP 互联（Web 一键互联向导 + Admin API）、`internal_topology` 不变量、route collector |
| [guides/dns-and-anycast.md](guides/dns-and-anycast.md) | DNS 组 / zone / record、`bind_addresses` 任播、共享组多节点 anycast、rDNS、CoreDNS |
| [guides/addressing-and-renumber.md](guides/addressing-and-renumber.md) | fleet 寻址分配（单播 /27 + 任播 /29）+ renumber 操作的同步点 + 迁移脚本 |
| [guides/upgrades-and-migrations.md](guides/upgrades-and-migrations.md) | agent wheel 构建与滚动升级、Control Server 升级、Alembic 迁移操作 |
| [guides/monitoring-and-troubleshooting.md](guides/monitoring-and-troubleshooting.md) | 健康五态判定、status-events、routing 视图、常见故障与 internal-interconnect postmortem |
| [guides/secret-recovery.md](guides/secret-recovery.md) | WireGuard 私钥 escrow（RSA-OAEP）模型 + `dn42-recover` 离线恢复流程 |
| [guides/web-ui.md](guides/web-ui.md) | Web 管理界面操作指南：登录、仪表盘、节点详情各页签、向导、审批、provision、审计 |

### 参考（reference/）

| 文档 | 内容 |
| --- | --- |
| [reference/api.md](reference/api.md) | 全部 Admin HTTP / Agent HTTP / WebSocket 接口 |
| [reference/configuration.md](reference/configuration.md) | Control Server 与 Node Agent 的全部配置项、环境变量、CLI |
| [reference/desired-state.md](reference/desired-state.md) | `DesiredState` 顶层与全部嵌套 schema 字段、校验规则、normalize 钩子 |
| [reference/database.md](reference/database.md) | 全部表、字段、关系、materialize 写入路径、迁移清单 |
| [reference/cli-and-scripts.md](reference/cli-and-scripts.md) | Node Agent CLI 全参数 + `deploy/` 运维脚本 + `scripts/` 开发脚本 |
| [reference/addressing-model.md](reference/addressing-model.md) | 地址概念模型：所有地址类型按**源 / 派生 / 副本**分类 |

### 内部原理（internals/）

| 文档 | 内容 |
| --- | --- |
| [internals/architecture.md](internals/architecture.md) | 系统组件、边界、数据流、最小扰动设计、并发一致性、变更闭环 |
| [internals/control-server.md](internals/control-server.md) | materializer、健康推导、token / enrollment、WebSocket / EventBus、Peering 聚合根、provision |
| [internals/node-agent.md](internals/node-agent.md) | 运行模式、守护双任务 + 门铃、planner / convergence、collectors、self-heal、sideline 任务 |
| [internals/shared-packages.md](internals/shared-packages.md) | 四个共享包分层与依赖方向、各包职责、关键模型 / 校验器 / 模板 |
| [internals/security.md](internals/security.md) | token 哈希模型、注册审批闸门、禁止的控制模型、写盘与容器安全 |

### 工程

| 文档 | 内容 |
| --- | --- |
| [contributing.md](contributing.md) | 测试分层与命令、golden 回归、文档维护约定、PR 流程 |
| [ROADMAP.md](ROADMAP.md) | 各能力当前状态 + 单一事实源重构进度（副本 → 派生） |

## 文档维护约定

1. **单一事实源**：每个主题只在一个文档里详细展开，其它地方用链接。配置只在 [reference/configuration.md](reference/configuration.md)、API 细节只在 [reference/api.md](reference/api.md)、字段只在 [reference/desired-state.md](reference/desired-state.md)、表结构只在 [reference/database.md](reference/database.md)。
2. **与代码同步**：改了接口、配置、表结构、运行模式、schema，必须同步对应参考文档；能力状态变化同步 [ROADMAP.md](ROADMAP.md)。
3. **示例可执行**：文档里的命令应当能在仓库根目录直接复制运行（PowerShell 为主，节点侧命令用 bash）。
4. **交叉引用代码**：用 `path:line` 指向源码（如 `apps/control-server/app/services/materializer.py:33`），方便从文档跳进实现。
5. 详细约定见 [contributing.md](contributing.md#文档维护约定)。
