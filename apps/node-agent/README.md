# node-agent

运行在 DN42 节点上的常驻执行器：读取 `DesiredState`，渲染配置，规划并部署到本机 Docker（直连 Docker Engine API，不用 docker-compose），做本机收敛（热重载 BIRD、重放 WireGuard 脚本），上报结果。

文档（单一事实源在 `docs/`）：

- 内部原理（运行模式、守护循环、planner/convergence、collectors、self-heal、旁路任务）：[../../docs/internals/node-agent.md](../../docs/internals/node-agent.md)
- CLI 全参数：[../../docs/reference/cli-and-scripts.md](../../docs/reference/cli-and-scripts.md) ｜ 配置：[../../docs/reference/configuration.md](../../docs/reference/configuration.md#node-agent)

代码结构速览：

| 路径 | 说明 |
| --- | --- |
| `agent/main.py` | CLI 解析、模式分发 |
| `agent/watch.py` | 守护循环：WS 订阅、门铃、consumer、旁路任务 |
| `agent/orchestrator.py` | `run_once()` 六阶段 |
| `agent/{planner,apply,collectors,render,secrets,health}/` | 决策 / 执行 / 观测 / 渲染 / WG 密钥 / 对账 |
| `agent/tests/` | Node Agent 测试 |

本地演练（不连控制面、不动机器）：

```bash
python -m agent.main --plan-only --state-dir .agent-state
```

常驻（生产默认）：`python -m agent.main --config /etc/dn42-control/agent.toml`。完整上手见 [../../docs/tutorials/01-quickstart.md](../../docs/tutorials/01-quickstart.md)。
