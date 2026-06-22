# control-server

FastAPI 控制服务：Admin / Agent API、数据库、token、`DesiredState` 合成与发布、WebSocket 事件、注册审批、健康与路由视图。

文档（单一事实源在 `docs/`）：

- 内部原理（materializer、健康五态、token、WS、Peering 聚合根）：[../../docs/internals/control-server.md](../../docs/internals/control-server.md)
- API：[../../docs/reference/api.md](../../docs/reference/api.md) ｜ 配置：[../../docs/reference/configuration.md](../../docs/reference/configuration.md#control-server) ｜ 数据库：[../../docs/reference/database.md](../../docs/reference/database.md)

代码结构速览：

| 路径 | 说明 |
| --- | --- |
| `app/main.py` | `create_app()`、FastAPI lifespan、service 初始化 |
| `app/core/` | `ControlServerConfig`、`EventBus` |
| `app/db/` | engine、ORM 模型、`provision_node_from_state()`、seed |
| `app/services/` | `materialize`、token、node_status、routing、pending_registrations、audit |
| `app/api/v1/` | `agent_http`、`agent_ws`、`admin/`（CRUD / provision / health / routing / tokens） |
| `app/tests/` | Control Server 测试 |

本地启动：

```bash
export DN42_CONTROL_SEED_BOOTSTRAP_NODE=1
uvicorn app.main:app --app-dir apps/control-server --reload --host 0.0.0.0 --port 8000
```
