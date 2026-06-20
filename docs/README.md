# 文档索引

本目录是 `dn42-control-backend` 的文档中心。文档按"读者要做什么"分四层：**入门教程 → 操作手册 → 参考 → 内部原理**。

## 我该读哪篇？

| 你的情况 | 从这里开始 |
| --- | --- |
| 第一次接触本项目 | [tutorial.md](tutorial.md) —— 大白话教程，从零跑通 |
| 要把系统部署到真实环境 | [operations.md](operations.md) —— 部署、节点接入、token、健康监控、排错 |
| 要用 Web 界面操作控制面 | [web-ui.md](web-ui.md) —— 登录、各页面、一键互联向导（含截图） |
| 节点间互联 / iBGP 缺路由 | [internal-interconnect.md](internal-interconnect.md) —— internal_topology 不变量、排错、postmortem |
| 要调用 / 对接 API | [api.md](api.md) —— 全部接口、请求响应示例 |
| 要查某个配置项 | [configuration.md](configuration.md) —— 两个应用的全部配置与环境变量 |
| 想理解系统怎么运转 | [architecture.md](architecture.md) —— 组件、边界、数据流、变更闭环 |
| 要改代码 / 写测试 | [testing.md](testing.md) + 各组件文档 |

## 全部文档

### 入门

| 文档 | 内容 |
| --- | --- |
| [tutorial.md](tutorial.md) | 新手教程：每个功能拆成「这是什么 / 为什么需要 / 一步步怎么做」 |

### 操作手册

| 文档 | 内容 |
| --- | --- |
| [operations.md](operations.md) | 部署（compose / systemd）、节点接入与审批、存量导入、token 生命周期、健康监控、故障排查 |
| [web-ui.md](web-ui.md) | 管理 Web UI 操作指南：登录、仪表盘、节点详情各页签、**一键互联向导**、注册审批、provision、审计（含截图） |
| [internal-interconnect.md](internal-interconnect.md) | 内部互联：`internal_topology` 驱动 iBGP/OSPF、**各节点配置必须一致**的不变量、加节点 checklist、缺路由排错 + 真实 postmortem |

### 参考

| 文档 | 内容 |
| --- | --- |
| [api.md](api.md) | Agent HTTP / WebSocket API、Admin API（CRUD、provision、注册审批、健康、token 管理） |
| [configuration.md](configuration.md) | Control Server 与 Node Agent 的全部配置项、环境变量、TOML、CLI 约束 |
| [desired-state.md](desired-state.md) | `DesiredState` 字段、校验规则、渲染结果 |
| [database.md](database.md) | 数据库表、字段、关系、Materializer / Provision 写入路径、Alembic |

### 内部原理

| 文档 | 内容 |
| --- | --- |
| [architecture.md](architecture.md) | 系统整体结构、内部组件、数据流、节点 runtime、变更闭环 |
| [addressing.md](addressing.md) | 地址概念总览：所有地址类型的用途 / 真相源 / 影响半径，按 源/派生/副本 分类（单一真相源重构基线） |
| [node-agent.md](node-agent.md) | Agent 运行模式、守护循环、部署 backend、本机收敛、模块边界、错误分层 |
| [security.md](security.md) | token 哈希模型、注册审批闸门、接口暴露边界、写盘与容器安全 |

### 工程

| 文档 | 内容 |
| --- | --- |
| [testing.md](testing.md) | 测试分层、常用命令、golden 文件、部署验证 |
| [TODO.md](TODO.md) | 待办事项与各能力当前状态 |

### 组件与共享包

| 文档 | 内容 |
| --- | --- |
| [../apps/control-server/README.md](../apps/control-server/README.md) | Control Server 代码结构与本地启动 |
| [../apps/node-agent/README.md](../apps/node-agent/README.md) | Node Agent 代码结构与命令示例 |
| [../packages/docs/README.md](../packages/docs/README.md) | 共享包分层、数据流与依赖方向 |
| [../packages/docs/dn42_schemas.md](../packages/docs/dn42_schemas.md) | 协议模型与枚举 |
| [../packages/docs/dn42_templates.md](../packages/docs/dn42_templates.md) | 模板渲染 |
| [../packages/docs/dn42_runtime.md](../packages/docs/dn42_runtime.md) | 文件计划、原子写盘、router Dockerfile 渲染 |
| [../packages/docs/dn42_common.md](../packages/docs/dn42_common.md) | 校验器与公共工具 |
| [../deploy/docker-compose/README.md](../deploy/docker-compose/README.md) | 三节点 compose 编排 |
| [../examples/rendered-hkg1/README.md](../examples/rendered-hkg1/README.md) | golden 渲染样本 |

## 文档维护约定

1. **单一事实来源**：每个主题只在一个文档里详细展开，其他地方用链接。例如配置项只在 [configuration.md](configuration.md) 维护，API 细节只在 [api.md](api.md) 维护。
2. **与代码同步**：改了接口、配置、表结构、运行模式，必须同步对应参考文档；能力状态变化同步 [TODO.md](TODO.md)。
3. **示例可执行**：文档中的命令应当能在仓库根目录直接复制运行（PowerShell 为主，节点侧命令用 bash）。
