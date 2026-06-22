# 部署

本文面向把系统跑到真实环境的操作员：部署形态、docker 全栈控制面、systemd 节点 agent、数据库（PostgreSQL）与缓存（Redis）、Web UI 托管。接入节点见 [node-onboarding.md](node-onboarding.md)，配置项见 [../reference/configuration.md](../reference/configuration.md)。

> 本文所有 `/api/v1/admin/*` 调用都需携带 `-H "Authorization: Bearer <DN42_CONTROL_ADMIN_TOKEN>"`，示例中为简洁省略。

## 部署形态总览

| 形态 | 适用 | 产物 |
| --- | --- | --- |
| 本地源码运行 | 开发调试 | `uvicorn` + `python -m agent.main`（见 [../tutorials/01-quickstart.md](../tutorials/01-quickstart.md)） |
| docker 全栈控制面 | 控制面后端一键起（control-server + PostgreSQL + Redis） | `docker/` |
| systemd 节点 agent | 真实 DN42 节点 | `deploy/systemd/` |

> **去 compose 澄清**：节点 runtime 的路由容器**不**用 docker-compose（Agent 直连 Docker Engine API 创建）。`docker/` 的 compose 只编排**控制面后端**（control-server + 库 + 缓存），**不含 node-agent**——agent 在真实节点上跑宿主原生 venv + systemd。

## docker 全栈控制面

`docker/` 提供控制面标准后端栈，一条 compose 拉起：

| 服务 | 说明 |
| --- | --- |
| `control-server` | FastAPI；连 postgres + redis，默认仅发布到 `127.0.0.1:${CONTROL_PORT:-8000}` |
| `postgres` 16 | 真后端库，持久卷 `pg-data`，仅容器网内可达 |
| `redis` 7 | 缓存（maxmemory + LRU，不持久化）；**旁路降级**——不可用时 control-server 自动回落 DB |

```bash
# 仓库根执行；先把样例 env 复制为 .env 填好密码/token
cp docker/.env.example docker/.env       # 至少改 POSTGRES_PASSWORD / DN42_CONTROL_ADMIN_TOKEN
docker compose -f docker/docker-compose.yml --env-file docker/.env up -d --build
```

启动顺序由 `depends_on: service_healthy` 保证：postgres + redis 先 healthy，control-server 再起；启动期 `create_all` 在空库建当前 schema（与 `alembic upgrade head` 等价，见下）。详见 [../../docker/README.md](../../docker/README.md)。

对外暴露：设 `CONTROL_BIND=0.0.0.0`（或经前置 TLS 反代），并务必用 `DN42_CONTROL_ADMIN_TOKEN` 保护 + 防火墙收窄入站源。关停：`down`（留卷保 DB）/ `down -v`（连数据卷删）。

## systemd 节点 agent

node-agent 在真实节点上跑**宿主原生 venv + systemd**（不走容器）——以便 apply 模式下渲染产物直接落宿主、宿主 dockerd 给路由容器做 bind 时路径一致，且 agent 直接访问宿主 `docker.sock`。`deploy/systemd/` 提供模板单元（注释内含完整安装步骤）：

| 单元 | 说明 |
| --- | --- |
| `dn42-node-agent@.service` | agent 模板单元：`%i` 即 `node_id`，同机可跑多实例；`Restart=always` 常驻 |
| `dn42-control-server.service` | 控制面也可走 systemd（uvicorn + **外部** postgres/redis），但推荐用上面的 docker 全栈 |

安装骨架（agent）：

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo mkdir -p /opt/dn42-control && sudo rsync -a ./ /opt/dn42-control/   # 或按单元注释路径
sudo python3 -m venv /opt/dn42-control/.venv
# 安装依赖 + 一方包（见单元文件 PYTHONPATH / ExecStart）
sudo systemctl daemon-reload
sudo systemctl enable --now dn42-node-agent@edge1
journalctl -u dn42-node-agent@edge1.service -f
```

agent 实例的节点专属变量放 `/etc/dn42-control/<node_id>.env`：

```text
DN42_AGENT_CONTROLLER_URL=https://control.example.dn42
DN42_AGENT_ENROLLMENT_TOKEN=change-me
```

## 数据库（PostgreSQL）+ 缓存（Redis）

控制面后端栈 = **control-server + PostgreSQL + Redis**。

- **后端库 PostgreSQL**：docker 全栈内置 `postgres:16`；或指向外部库 `DN42_CONTROL_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db`。
  - ⚠️ ASN 等列用 `BigInteger`（DN42 ASN 4242420000+ 超 PostgreSQL int32 上限）。
- **缓存 Redis**：docker 全栈内置 `redis:7`；或外部 `DN42_CONTROL_REDIS_URL=redis://host:6379/0`。缓存 desired-state（generation 键）/ 节点健康 / 路由聚合等高频读，写时主动失效。**未配置或不可用即全程 no-op、回落 DB**，不影响正确性。
- **建表**：启动期 `Base.metadata.create_all` 建缺失表（与 alembic 等价）；规范化用 Alembic：

```bash
# 仓库根执行（DSN 取自环境变量）
export DN42_CONTROL_DATABASE_URL=postgresql+asyncpg://user:pass@host/db
alembic upgrade head
```

- **SQLite → PostgreSQL 迁移**（旧库升级）：`docker/migrate_sqlite_to_postgres.py`——按 FK 拓扑序整库拷贝（类型安全）+ 重置自增序列，源库只读。

迁移清单、`create_all` 与 Alembic 的等价性、跨 schema 升级见 [upgrades-and-migrations.md](upgrades-and-migrations.md) 与 [../reference/database.md](../reference/database.md#迁移alembic)。

## 生产必做项

- **后端库用 PostgreSQL**（docker 全栈内置，或外部 DSN）——勿在生产用默认 SQLite。
- **配 Redis 缓存**（docker 全栈内置）——可选但推荐，卸高频读。
- **必设 `DN42_CONTROL_ADMIN_TOKEN`**：不设则 admin API 整体 403（fail-closed）。
- **改掉 `DN42_CONTROL_ENROLLMENT_TOKEN` / `POSTGRES_PASSWORD` 默认值**。
- **配 recovery 公钥**：`DN42_CONTROL_RECOVERY_PUBLIC_KEY`（PEM 文本或文件路径），用于 WG 私钥 escrow，见 [secret-recovery.md](secret-recovery.md)。
- **收紧 CORS**：`DN42_CONTROL_CORS_ORIGINS` 只列 Web UI 的来源。
- **网络层保护**：admin API 前置 TLS 反代 + 防火墙收窄入站源，见 [../internals/security.md](../internals/security.md)。

全部环境变量见 [../reference/configuration.md](../reference/configuration.md#control-server)。

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
