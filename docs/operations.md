# 部署与运维

本文面向把系统跑到真实环境的操作员：怎么部署控制面和 agent、怎么把节点接进来、怎么管 token、怎么看健康、怎么排错。

> 偏向**用浏览器点界面**操作（建对等连接、审批、看健康）请配合 [web-ui.md](web-ui.md)；节点间互联
> （iBGP/OSPF）的配置与排错见 [internal-interconnect.md](internal-interconnect.md)。

## 部署形态总览

| 形态 | 适用场景 | 产物 |
| --- | --- | --- |
| 本地源码运行 | 开发调试 | `uvicorn` + `python -m agent.main` |
| docker compose 三节点 | 控制面多节点行为联调 / 演示 | `deploy/docker-compose/` |
| systemd 生产部署 | 真实 DN42 节点 | `deploy/systemd/` |

## 本地开发运行

```bash
cd dn42-control-backend
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# 带内置 demo 节点启动控制面（默认不播种，见 configuration.md）
export DN42_CONTROL_SEED_BOOTSTRAP_NODE=1
uvicorn app.main:app --app-dir apps/control-server --reload --host 0.0.0.0 --port 8000
```

另一个终端跑 agent（只演练）：

```bash
python -m agent.main \
  --controller-url http://127.0.0.1:8000 \
  --enrollment-token enroll-token \
  --requested-node-id edge1 \
  --state-dir .agent-state \
  --plan-only
```

## docker compose 三节点联调

`deploy/docker-compose/` 提供 1 个 control-server + 1 个一次性 provisioner + 3 个常驻 agent（`edge1` / `edge2` / `edge3`）的编排，详见 [deploy/docker-compose/README.md](../deploy/docker-compose/README.md)。

```bash
# 仓库根执行
cp deploy/docker-compose/.env.example deploy/docker-compose/.env   # 可选
docker compose -f deploy/docker-compose/docker-compose.three-node.yml up -d --build
```

启动顺序由 `depends_on` 保证：

1. `control-server` 起来并通过 `/healthz` 健康检查；
2. `provisioner` 一次性把三个内部节点的完整 `DesiredState` 通过
   `POST /api/v1/admin/provision` 灌入控制面，然后退出（脚本：`scripts/dev/provision-three-node.py`）；
3. 三个 agent 常驻：先注册拿 token，再连各自私有 WS 通道
   `/api/v1/agent/ws/{node_id}`，收到 `desired_state_updated` 事件即重新 reconcile。

观察行为：

```bash
# 看某个 agent 的常驻日志（注册 → 连 WS → reconcile）
docker compose -f deploy/docker-compose/docker-compose.three-node.yml logs -f agent-hkg1

# 触发某节点的世代递增，只有对应 agent 会收到事件（验证私有通道隔离）
curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/notify" \
  -H "Content-Type: application/json" \
  -d '{"event": "desired_state_updated"}'

# 查看 agent 渲染出的配置（落在状态卷里）
docker compose -f deploy/docker-compose/docker-compose.three-node.yml exec agent-hkg1 \
  ls -R /var/lib/dn42-control/nodes/edge1/rendered
```

> 该编排中 agent 显式以 `DN42_AGENT_MODE=write-rendered` 运行：只把配置渲染到
> 状态卷，不真正起路由容器，因此无需 docker-in-docker，可在任意 Docker 主机
> 安全演示控制面多节点行为。要让 agent 真正落地路由容器：改
> `DN42_AGENT_MODE=apply` 并给 agent 服务挂载 `/var/run/docker.sock`。

关停：

```bash
docker compose -f deploy/docker-compose/docker-compose.three-node.yml down -v
```

## systemd 生产部署

`deploy/systemd/` 提供两个单元文件，注释里有完整安装步骤：

| 单元 | 说明 |
| --- | --- |
| `dn42-control-server.service` | 控制面：uvicorn 监听 `:8000`，状态目录 `/var/lib/dn42-control`，带 `ProtectSystem=strict` 等加固 |
| `dn42-node-agent@.service` | agent 模板单元：`%i` 即 `node_id`，同机可跑多个实例；`Restart=always` 常驻 |

agent 实例的节点专属变量放 `/etc/dn42-control/<node_id>.env`：

```text
DN42_AGENT_CONTROLLER_URL=https://control.example.dn42
DN42_AGENT_ENROLLMENT_TOKEN=change-me
```

启用：

```bash
sudo systemctl enable --now dn42-control-server
sudo systemctl enable --now dn42-node-agent@edge1
journalctl -u dn42-node-agent@edge1.service -f
```

生产建议：

- `DN42_CONTROL_DATABASE_URL` 指向 PostgreSQL（`postgresql+asyncpg://...`），
  并用 `alembic upgrade head` 管理表结构，而非依赖启动期 `create_all`。
- `DN42_CONTROL_ENROLLMENT_TOKEN` 必须改掉默认值。
- 必须设置 `DN42_CONTROL_ADMIN_TOKEN`（不设置则 admin API 整体 403 fail-closed）；建议再叠加网络层保护（见 [security.md](security.md#admin-api-保护)）。

> **注意**：本文所有 `/api/v1/admin/*` 调用都需要携带
> `-H "Authorization: Bearer <DN42_CONTROL_ADMIN_TOKEN>"`，示例中为简洁省略。

## 节点接入方式总览

| 方式 | 适用 | 入口 |
| --- | --- | --- |
| 存量配置导入 | 已有传统方式配好的节点（bird/wireguard 文件） | `scripts/tools/import_node_config.py` |
| 整节点 provision | 已有完整 `DesiredState` JSON | `POST /api/v1/admin/provision` |
| 逐资源 CRUD | 从零精细搭建 | `POST /admin/nodes` + peerings/interfaces/... |
| demo seed | 本地开发练手 | `DN42_CONTROL_SEED_BOOTSTRAP_NODE=1` |

### 存量节点导入

把一台已有节点的配置文件一次性"读"进控制面（推荐走 HTTP，不直接碰数据库）：

```bash
python scripts/tools/import_node_config.py <配置目录> \
  --node-id edge1 \
  --controller-url http://127.0.0.1:8000 \
  --agent-token my-secret-token
```

| 参数 | 含义 |
| --- | --- |
| `<配置目录>` | 现有 bird / wireguard 配置文件所在文件夹 |
| `--node-id` | 给这台机器起的唯一名字 |
| `--controller-url` | 控制面地址；填了就走 `POST /admin/provision`，不直连数据库 |
| `--agent-token` | 给该节点配的固定 token，agent 之后用它登录 |
| `--wg-port-range` | WireGuard 监听端口范围（默认 `51800-51899`），入站 peer 必须落在范围内 |
| `--dry-run` | 只解析、打印结果，不真正写入 |

先加 `--dry-run` 检查解析出的 `DesiredState` 是否正确，再去掉它正式导入。

### 新节点接入与审批

控制面不认识的新机器第一次注册时不会被直接放行，而是进入待审批队列（安全闸门，见 [security.md](security.md#注册审批闸门)）：

1. 新机器的 agent 调用 `/agent/register` → 控制面记入待审批名单，返回 `pending-approval`。
2. 管理员查看队列并批准：

   ```bash
   curl -s "http://127.0.0.1:8000/api/v1/admin/registrations?status=pending"

   curl -s -X POST \
     "http://127.0.0.1:8000/api/v1/admin/registrations/3/approve" \
     -H "Content-Type: application/json" -d '{"note": "确认是我的机器"}'
   ```

3. **批准 ≠ 直接能用**。还需要给它下发配置：用上面的导入脚本，或
   `POST /admin/provision` 灌一份 `DesiredState`。
4. 下发完成后，该节点的 agent 下次注册即返回 `accepted` 并拿到 token，开始正常工作。

### 节点退役

直接 `DELETE` 一个**已发布过、仍在运行**的节点会留下孤儿——它本地的容器、WireGuard
隧道、BGP 会话还在向 fleet 宣告路由，但控制面已经不认识它了。因此退役分两步：

1. **先退役收敛**：

   ```bash
   curl -s -X POST \
     "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/decommission"
   ```

   控制面把节点标记为 `decommissioned` 并下发一份**空对端**的 `DesiredState`
   （`interfaces=[]` / `bgp_sessions=[]` / `dns=null`）。agent 下一轮 reconcile 即拆除
   所有隧道、撤掉所有 BGP 会话——**节点停止宣告任何路由**。核心容器（router-netns /
   wg-gateway / bird-router）保留为惰性空转（schema 强制这三个角色必须在；它们没有
   对端，不再参与路由）。子表配置原样保留，可 `recommission` 撤销退役恢复。

2. **确认收敛后再删**：通过 `/admin/nodes/{id}/health` 确认 agent 已应用退役态，再

   ```bash
   curl -s -X DELETE "http://127.0.0.1:8000/api/v1/admin/nodes/edge1"
   ```

   未退役的 active 节点直接 DELETE 会被拒（409），提示先 decommission。从未发布过
   （`current_generation == 0`）的节点没有部署，可直接删除。

> 物理下线：节点停止宣告路由后，惰性的核心容器随宿主机一并下线即可（停 agent、
> `docker rm`），它们不影响 fleet。

### Token 生命周期

token 安全模型见 [security.md](security.md#agent-token-哈希存储)，接口细节见 [api.md](api.md#agent-token-管理)。常用操作：

```bash
# 签发（可设 7 天过期）；响应里的 secret 只出现这一次
curl -s -X POST \
  "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/agent-tokens" \
  -H "Content-Type: application/json" -d '{"ttl_seconds": 604800}'

# 查元信息（无 secret）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/agent-tokens"

# 轮换 / 撤销
curl -s -X POST "http://127.0.0.1:8000/api/v1/admin/agent-tokens/<token_id>/rotate"
curl -s -X DELETE "http://127.0.0.1:8000/api/v1/admin/agent-tokens/<token_id>"
```

轮换后需要把新 secret 更新到节点的 `identity.json`（或删除 identity 让 agent 重新注册）。

## 健康监控

控制面持久化每台机器的快照 / 对账 / 应用结果，并自动推导健康：

| 状态 | 含义 |
| --- | --- |
| `ok` | 一切正常，实际状态 = 期望状态 |
| `stale` | 落后：还没追上最新配置，或超过 900 秒没上报 |
| `degraded` | 应用失败、或检测到配置漂移 |
| `unknown` | 还没收到过任何上报 |

```bash
# 机群概览
curl -s "http://127.0.0.1:8000/api/v1/admin/health"

# 单节点详细健康（最近一次 snapshot/report/apply）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/health"

# 历史事件（排错神器；kind 可选 snapshot / report / apply）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/status-events?kind=apply&limit=20"
```

## 常见故障排查

| 现象 | 可能原因 / 怎么查 |
| --- | --- |
| agent 连不上控制面 | 检查 `controller_url` 是否可达；常驻模式**必须**配 `controller_url` |
| 新机器注册后没反应 | 它在等审批：查 `/admin/registrations?status=pending` |
| agent 报 `pending-approval` 循环 | 已批准但还没 provision；下发 `DesiredState` 后它才能拿 token |
| 健康显示 `stale` | 没追上最新 generation 或太久没上报；看 `status-events` 最后一次时间 |
| 健康显示 `degraded` | 应用失败或有漂移；看 `status-events?kind=apply` 的报错 |
| BGP 会话突然全断又自己好了 | 多半是隧道/容器重建触发，1～3 分钟自愈，属正常 |
| WireGuard 一直握不上手（handshake=0） | 入站 peer 的监听端口没在 `wireguard_port_range` 内、没对外发布 |
| 某节点比同 AS 节点缺路由（`route_count` 不一致） | 多半各节点 `internal_topology` 不一致、iBGP 没全互联；排错与 postmortem 见 [internal-interconnect.md](internal-interconnect.md) |
| token 失效（401） | 过期或被轮换/撤销；重新签发并更新节点 identity |
| 想先演练不动机器 | 用 `--plan-only` 跑一遍看计划 |
| Docker 部署失败 | agent 所在主机能否访问 Docker socket；看 apply-result 的 `errors` |

节点本机排查：

```bash
journalctl -u dn42-node-agent@edge1.service -n 120 --no-pager
docker ps --filter label=dn42.managed=true
docker logs dn42-edge1-dn42-wg-gateway-1 --tail 120
docker exec dn42-edge1-dn42-wg-gateway-1 wg show
docker exec dn42-edge1-dn42-bird-router-1 birdc show protocols
```

## 开发辅助脚本

| 脚本 | 作用 |
| --- | --- |
| `scripts/dev/provision-three-node.py` | 把三节点 lab 的 `DesiredState` 灌入控制面（compose provisioner 用） |
| `scripts/dev/render-local-two-node.py` | 渲染两节点 lab 的配置到本地目录 |
| `scripts/dev/render-local-three-node.py` | 渲染三节点 lab 的配置到本地目录 |
| `scripts/dev/render-two-internal-one-ebgp-demo.py` | 渲染"两内部节点 + 一个 eBGP 对端"演示拓扑 |
| `scripts/tools/import_node_config.py` | 存量节点配置导入（见上文） |
