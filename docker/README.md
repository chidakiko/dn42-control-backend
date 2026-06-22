# DN42 控制面部署（docker compose）

控制面**标准全栈**：`control-server + PostgreSQL + Redis`，一条 compose 拉起。

| 文件 | 作用 |
| --- | --- |
| `docker-compose.yml` | 全栈编排：control-server（FastAPI）+ postgres（真后端库）+ redis（缓存） |
| `Dockerfile.control-server` | 控制面镜像（uvicorn 跑 FastAPI；`pip install` 首方包 + 第三方依赖） |
| `.env.example` | 可调参数样例（DB 密码 / enrollment / admin token / 端口） |

`build context` 是仓库根。control-server 连 postgres（`postgresql+asyncpg://…`）与 redis
（`redis://redis:6379/0`）；缓存是旁路，redis 不可用时自动回落 DB，不影响正确性。

> node-agent 不在此编排：生产里 agent 跑宿主原生 venv + systemd、直连宿主 docker.sock；
> 本栈只含控制面 + 库 + 缓存。systemd 生产部署见 [部署指南](../docs/guides/deployment.md)。

## 启动

```bash
# 仓库根执行；先把样例 env 复制为 .env 填好密码/token
cp docker/.env.example docker/.env
$EDITOR docker/.env   # 至少改 POSTGRES_PASSWORD / DN42_CONTROL_ADMIN_TOKEN

docker compose -f docker/docker-compose.yml --env-file docker/.env up -d --build
```

启动顺序由 `depends_on: service_healthy` 保证：postgres + redis 先 healthy，control-server
再起；启动期 `create_all` 在空库建当前 schema（与 `alembic upgrade head` 等价）。

默认仅发布到 `127.0.0.1:${CONTROL_PORT:-8000}`。对外暴露：设 `CONTROL_BIND=0.0.0.0`（或经前置
反代），并务必用 `DN42_CONTROL_ADMIN_TOKEN` 保护 admin API + 在防火墙/安全组收窄入站源。

## 验证

```bash
# 存活 + DB 连通探针
curl http://127.0.0.1:8000/healthz

# admin API（带 Bearer = .env 的 DN42_CONTROL_ADMIN_TOKEN）
curl -H "Authorization: Bearer <ADMIN_TOKEN>" http://127.0.0.1:8000/api/v1/admin/health

# 缓存命中（第二次起从 redis 返回）
docker compose -f docker/docker-compose.yml exec redis redis-cli info stats | grep keyspace
```

## 关停

```bash
docker compose -f docker/docker-compose.yml -p <project> down       # 留卷（保 DB）
docker compose -f docker/docker-compose.yml -p <project> down -v     # 连数据卷一起删
```

## 数据库

- **后端库**：PostgreSQL（compose 内置 `postgres:16`，持久卷 `pg-data`）。ASN 等列用
  `BigInteger`（DN42 ASN 超 int32）。
- **SQLite→PG 迁移**：旧 SQLite 库迁移用 `docker/migrate_sqlite_to_postgres.py`（FK 拓扑序
  整库拷贝 + 重置自增序列）。
- **缓存**：Redis（`redis:7`，maxmemory + LRU，不持久化）。缓存 desired-state / 健康 / 路由
  聚合等高频读，写时主动失效。
