# 部署

本文面向把系统跑到真实环境的操作员：三种部署形态、生产 systemd 部署、数据库与迁移、Web UI 托管。接入节点见 [node-onboarding.md](node-onboarding.md)，配置项见 [../reference/configuration.md](../reference/configuration.md)。

> 本文所有 `/api/v1/admin/*` 调用都需携带 `-H "Authorization: Bearer <DN42_CONTROL_ADMIN_TOKEN>"`，示例中为简洁省略。

## 部署形态总览

| 形态 | 适用 | 产物 |
| --- | --- | --- |
| 本地源码运行 | 开发调试 | `uvicorn` + `python -m agent.main`（见 [../tutorials/01-quickstart.md](../tutorials/01-quickstart.md)） |
| docker compose 多节点 | 控制面多节点行为联调 / 演示 | `deploy/docker-compose/` |
| systemd 生产部署 | 真实 DN42 节点 | `deploy/systemd/` |

> **去 compose 澄清**：节点 runtime 的容器**不**用 docker-compose（Agent 直连 Docker Engine API 创建）。`deploy/docker-compose/` 仅用于把 control-server + agent 容器化起来做多节点**联调/演示**，不是生产路径，也未废弃。

## docker compose 多节点联调

`deploy/docker-compose/` 提供 1 个 control-server + 1 个一次性 provisioner + 多个常驻 agent 的编排，详见 [../../deploy/docker-compose/README.md](../../deploy/docker-compose/README.md)。

```bash
# 仓库根执行
cp deploy/docker-compose/.env.example deploy/docker-compose/.env   # 可选
docker compose -f deploy/docker-compose/docker-compose.three-node.yml up -d --build
```

启动顺序由 `depends_on` 保证：control-server 通过 `/healthz` → provisioner 一次性 `POST /api/v1/admin/provision` 灌入各节点后退出（`scripts/dev/provision-three-node.py`）→ 各 agent 常驻注册、连私有 WS、收事件 reconcile。

> 该编排默认 `DN42_AGENT_MODE=write-rendered`：只渲染到状态卷、不起路由容器，无需 docker-in-docker，可在任意 Docker 主机安全演示。要真正落地容器：改 `DN42_AGENT_MODE=apply` 并给 agent 挂 `/var/run/docker.sock`。

关停：`docker compose -f deploy/docker-compose/docker-compose.three-node.yml down -v`。

## systemd 生产部署

`deploy/systemd/` 提供两个单元文件（注释内含完整安装步骤）：

| 单元 | 说明 |
| --- | --- |
| `dn42-control-server.service` | 控制面：uvicorn 监听 `:8000`，状态目录 `/var/lib/dn42-control`，带 `ProtectSystem=strict`、`NoNewPrivileges=true` 等加固 |
| `dn42-node-agent@.service` | agent 模板单元：`%i` 即 `node_id`，同机可跑多实例；`Restart=always` 常驻 |

安装骨架：

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo mkdir -p /opt/dn42-control && sudo rsync -a ./ /opt/dn42-control/   # 或按单元注释路径
sudo python3 -m venv /opt/dn42-control/.venv
# 安装依赖 + 一方包（见单元文件 PYTHONPATH / ExecStart）
sudo systemctl daemon-reload
sudo systemctl enable --now dn42-control-server
sudo systemctl enable --now dn42-node-agent@edge1
journalctl -u dn42-node-agent@edge1.service -f
```

agent 实例的节点专属变量放 `/etc/dn42-control/<node_id>.env`：

```text
DN42_AGENT_CONTROLLER_URL=https://control.example.dn42
DN42_AGENT_ENROLLMENT_TOKEN=change-me
```

## 生产必做项

- **数据库用 PostgreSQL**：`DN42_CONTROL_DATABASE_URL=postgresql+asyncpg://...`，并用 `alembic upgrade head` 管理表结构，而非依赖启动期 `create_all`（见下）。
- **必设 `DN42_CONTROL_ADMIN_TOKEN`**：不设则 admin API 整体 403（fail-closed）。
- **改掉 `DN42_CONTROL_ENROLLMENT_TOKEN` 默认值**。
- **配 recovery 公钥**：`DN42_CONTROL_RECOVERY_PUBLIC_KEY`（PEM 文本或文件路径），用于 WG 私钥 escrow，见 [secret-recovery.md](secret-recovery.md)。
- **收紧 CORS**：`DN42_CONTROL_CORS_ORIGINS` 只列 Web UI 的来源。
- **网络层保护**：admin API 前置 TLS 反代，见 [../internals/security.md](../internals/security.md)。

全部环境变量见 [../reference/configuration.md](../reference/configuration.md#control-server)。

## 数据库与迁移

- 开发态：control-server 启动时 `Base.metadata.create_all` 建缺失表（够本地用）。
- **生产态**：用 Alembic。迁移与 control-server 共享 `DN42_CONTROL_DATABASE_URL`（`migrations/env.py`）。

```bash
# 仓库根执行（DSN 取自环境变量）
export DN42_CONTROL_DATABASE_URL=postgresql+asyncpg://user:pass@host/db
alembic upgrade head
```

迁移清单、`create_all` 与 Alembic 的差异、以及跨 schema 升级的坑见 [upgrades-and-migrations.md](upgrades-and-migrations.md) 与 [../reference/database.md](../reference/database.md#迁移alembic)。

## Web UI 托管

Web UI（`apps/web`）是 SvelteKit + `adapter-static` 的**纯静态 SPA**，与 control-server **分开托管**（control-server 不挂 StaticFiles）：

```bash
cd apps/web
npm ci && npm run build      # 产物在 apps/web/build/
```

把 `build/` 用任意静态服务器（nginx / Caddy）伺服，前端经 **CORS + Bearer**（token 存 localStorage）直连 control-server `/api/v1/*`。因此：

- control-server 的 `DN42_CONTROL_CORS_ORIGINS` 必须包含 Web UI 的来源地址。
- SPA 路由需服务器 fallback 到 `index.html`（adapter-static 已生成 fallback）。

操作指南见 [web-ui.md](web-ui.md)。
