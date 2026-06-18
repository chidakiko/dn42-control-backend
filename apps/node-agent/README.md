# Node Agent

Node Agent 是运行在 DN42 节点上的执行器。它读取 `DesiredState`，渲染配置文件，计算文件和容器变更，部署到本机 Docker，做本机收敛（热重载 BIRD、重放 WireGuard 脚本），并把结果上报给 Control Server。

完整说明见 [../../docs/node-agent.md](../../docs/node-agent.md)，配置见 [../../docs/configuration.md](../../docs/configuration.md#node-agent)。

## 快速运行

> 默认（不带 flag）即**常驻守护进程**：先 reconcile 一次，再连控制面 WebSocket，
> 收到事件就自动 reconcile 并真实部署。reconcile 深度由 `--mode` 控制
> （`apply` 默认 / `write-rendered` / `plan-only`）。

只使用内置 hkg1 示例演练，不连接 Control Server、不动本机：

```bash
python -m agent.main --plan-only --state-dir .agent-state
```

连接 Control Server，只做规划：

```bash
python -m agent.main \
  --controller-url http://127.0.0.1:8000 \
  --enrollment-token enroll-token \
  --requested-node-id edge1 \
  --state-dir .agent-state \
  --plan-only
```

连接 Control Server，单次完整部署后退出：

```bash
python -m agent.main \
  --controller-url http://127.0.0.1:8000 \
  --enrollment-token enroll-token \
  --requested-node-id edge1 \
  --state-dir .agent-state \
  --once
```

常驻守护进程（生产默认形态）：

```bash
python -m agent.main --config /etc/dn42-control/agent.toml
```

## 配置

配置来源优先级：默认值 → `agent.toml` → 环境变量 `DN42_AGENT_*` → CLI 参数。
全部配置项见 [../../docs/configuration.md](../../docs/configuration.md#node-agent)，示例配置见 `agent.example.toml`。

## 部署方式

agent 通过 Python Docker SDK 直连 Docker Engine API：创建网络、构建镜像、
按依赖拓扑创建容器，以及容器内 exec / 文件推送，全部走同一个 socket，
**不依赖 docker / docker compose CLI 二进制**。

容器编排完全由数据驱动：容器定义（网络、端口、挂载、依赖）以
`DesiredState.runtime` 的结构化数据从控制面数据库直达 Engine API，
router 镜像的 Dockerfile 也由 agent 按 `runtime.router_dockerfile` 在内存
生成、经 Engine API `fileobj` 构建。渲染目录只包含配置文件,**不存在任何
编排文件或构建文件**。

## 测试

```bash
python -m pytest apps/node-agent/agent/tests -q
```
