# 监控与排错

本文讲怎么看 fleet 健康、怎么定位常见故障，以及一条**容易被破坏、破坏后很隐蔽**的内部互联不变量（附真实 postmortem）。健康推导原理见 [../internals/control-server.md](../internals/control-server.md#健康推导五态)。

> 所有 `/api/v1/admin/*` 调用需携带 `-H "Authorization: Bearer <DN42_CONTROL_ADMIN_TOKEN>"`，示例省略。

## 健康监控

控制面持久化每台机器的快照 / 对账 / 应用结果，自动推导**五态**健康：

| 状态 | 含义 |
| --- | --- |
| `ok` | 一切正常，实际状态 = 期望状态 |
| `stale` | 落后：还没追上最新 generation，或超过 `stale_after`（默认 900s）没上报 |
| `degraded` | 应用失败、上报 degraded、或检测到配置漂移（drift） |
| `down` | 超过 `down_after`（默认 3600s）完全静默（`unknown` 除外） |
| `unknown` | 还没收到过任何上报 |

阈值可配（`DN42_CONTROL_HEALTH_STALE_AFTER` / `_DOWN_AFTER`，见 [../reference/configuration.md](../reference/configuration.md)），读取时叠加，改阈值无需改库。

```bash
# 机群概览
curl -s "http://127.0.0.1:8000/api/v1/admin/health"
# 单节点详细健康（最近一次 snapshot/report/apply）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/health"
# 历史事件（排错神器；kind 可选 snapshot / report / apply / reresolve）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/status-events?kind=apply&limit=20"
```

## 路由观测

```bash
# fleet 路由概览
curl -s "http://127.0.0.1:8000/api/v1/admin/routing/fleet"
# 单节点路由摘要（含 peers 分桶——最优路由来源）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/routing/summary"
# 起源 AS / 前缀翻页 / 时间线
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/routing/origins"
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/routing/prefixes?limit=1000&offset=0"
```

`peers` 分桶（最优路由来源计数）是判断"某节点在不在用 iBGP / 某条会话"的最快信号。明细存索引表 `node_route_entries`（见 [../reference/database.md](../reference/database.md#路由)）。

## 常见故障排查

| 现象 | 可能原因 / 怎么查 |
| --- | --- |
| agent 连不上控制面 | 检查 `controller_url` 可达；常驻模式**必须**配 `controller_url` |
| 新机器注册后没反应 | 在等审批：查 `/admin/registrations?status=pending` |
| agent 报 `pending-approval` 循环 | 已批准但还没 provision；下发 `DesiredState` 后才能拿 token |
| 健康 `stale` | 没追上最新 generation 或太久没上报；看 `status-events` 最后时间 |
| 健康 `degraded` | 应用失败或有漂移；看 `status-events?kind=apply` 的报错 |
| 健康 `down` | 超过 `down_after` 完全静默；检查 agent 进程 / 网络 |
| BGP 全断又自愈 | 多半隧道/容器重建触发，1～3 分钟自愈，属正常 |
| WireGuard 握不上手（handshake=0） | 入站 peer 监听端口没在 `wireguard_port_range` 内、没对外发布；若对端是动态 IP，等 reresolve（45s） |
| 某节点比同 AS 节点缺路由 | 多半各节点 `internal_topology` 不一致、iBGP 没全互联（见下） |
| token 失效（401） | 过期或被轮换/撤销；agent 401 自愈会重注册，或重新签发更新 identity |
| 想先演练不动机器 | 用 `--plan-only` 跑一遍看计划 |
| Docker 部署失败 | agent 主机能否访问 Docker socket；看 apply-result 的 `errors` |

节点本机排查：

```bash
journalctl -u dn42-node-agent@edge1.service -n 120 --no-pager
docker ps --filter label=dn42.managed=true
docker exec dn42-edge1-dn42-wg-gateway-1 wg show
docker exec dn42-edge1-dn42-bird-router-1 birdc show protocols
```

容器名固定 `dn42-<node_id>-dn42-<role>-1`。

---

# 内部互联：iBGP / OSPF / internal_topology

同一个 AS 内多节点之间的互联（iBGP 交换路由 + OSPF 提供 loopback 可达）由 `DesiredState.bird.internal_topology` 合成，**不走 `bgp_sessions`**（那是 eBGP 对外 peering，见 [peering.md](peering.md)）。

## 是什么

`internal_topology`（`InternalTopologySpec`，见 [../reference/desired-state.md](../reference/desired-state.md)）驱动 `ibgp.conf.j2` + `ospf.conf.j2`：

| 字段 | 作用 |
| --- | --- |
| `routers` | 参与内部路由域的节点名列表。`full_mesh_ibgp=true` 时对除自己外每个建一条 iBGP |
| `hosts` | 节点名 → `{ownip, ownip6, ibgp_rr_upstreams}`。`ownip/ownip6` 是该节点 loopback（iBGP next-hop / neighbor）。`ibgp_rr_upstreams` 非空表示本节点是某 RR 的 client |
| `igp_adjacencies` | **本节点自己的**物理内部链路 `{node, interface, cost, iface_type}`。OSPF 邻接只在这些真实接口上形成 |
| `full_mesh_ibgp` / `ospf_v2` / `ospf_v3` / `private_nodes` | 拓扑开关 |

iBGP 跑在 **loopback** 上（next-hop-self），不需两节点物理直连——但**需要 OSPF 把对端 loopback 传过来**，否则 neighbor 不可达起不来。所以 OSPF 是 iBGP 的前提。

## 不变量（最重要）

> **同一个 AS 内，所有节点的 `internal_topology.routers` + `hosts` 必须是同一份完整集合。**

每个节点**只**从自己这份 `internal_topology` 生成 iBGP/OSPF，不会自动发现别的节点。各节点不一致就会各建"部分网格"，导致某些节点缺路由——而且 eBGP 不受影响、节点 health 正常，缺失很隐蔽。`igp_adjacencies` 是例外（每节点各自的物理链路本就不同）。

拓扑选择：
- **full-mesh iBGP**（节点少时推荐）：所有节点 `routers` 含全员、`full_mesh_ibgp=true`、`hosts` 含全员 loopback、`ibgp_rr_upstreams` 全空。
- **route reflector**：选一节点当 RR，其余在 `hosts[self].ibgp_rr_upstreams` 指向它。**要么全 full-mesh、要么所有节点对 RR 关系一致**——别留半成品。

## 加内部节点 checklist

1. 给新节点和某已有节点之间建内部 WG 隧道（p2p /31 + /127 + `fe80::` link-local），双方各加一条 `igp_adjacencies` 指向对方接口。
2. **把新节点加进所有已有节点**的 `routers` 和 `hosts`，并把全员补进新节点自己的 `routers`/`hosts`。漏任何一个就缺路由。
3. 下发后到 `/routing/summary` 确认：新节点最优路由里出现 iBGP 来源、各节点 `route_count` 一致。

> 参考 `deploy/unify_internal_topology.py`：把全 fleet 的 `routers`/`hosts` 统一成同一份 full-mesh，保留各节点自己的 `igp_adjacencies`。⚠️ 历史版本硬编码旧地址，renumber 后须先更新。见 [../reference/cli-and-scripts.md](../reference/cli-and-scripts.md)。

---

## Postmortem：pvg2 比同 AS 节点少 12–15 条路由（2026-06-16）

**现象**：4 节点同属 AS4242420000。can2/hkg1/hkg2 逐位一致 2216 条，pvg2 只有 2201。诡异点：纯 iBGP 学习的 hkg2 反而满，有自己 eBGP 的 pvg2 却缺。

**根因**：物理内网是一条链 `pvg2 — can2 — hkg2 — hkg1`，而四节点 `internal_topology` 各不一致：pvg2 的 routers 只有 `{pvg2, can2}`，根本不认识 hkg1/hkg2；hkg2 被配成半成品 RR。hkg2 作为 RR 把 hkg1↔can2 互相反射，所以那三家满。但 **can2 不是 RR**：它从 hkg2 学到的 hkg1 路由不会再转发给 pvg2（iBGP 水平分割）。pvg2 只挂 can2，于是拿不到只经 hkg1 反射的那批前缀。

**诊断（全程只读 API）**：
1. `/routing/summary` 的 `peers` 分桶——pvg2 iBGP 来源 0 条，一眼看出没用任何 iBGP 路由。
2. `/nodes/{id}/internal-topology`——暴露 pvg2 只有 can2、四节点 routers/hosts 不一致。
3. `/routing/prefixes` 翻页求差集——确认 pvg2 是 hkg2 严格子集，缺的全是 `via ibgp_hkg1`。

**修复**：四节点统一 full-mesh（`routers=[pvg2,can2,hkg2,hkg1]`、`hosts` 含四 loopback、丢掉半成品 RR、`full_mesh_ibgp=true`，`igp_adjacencies` 不动），脚本 `deploy/unify_internal_topology.py`。结果 30 秒内 pvg2 收敛到 2216，eBGP 全程不受影响。

**教训**：
- **iBGP 不中继**：从一个 iBGP 邻居学到的路由不会自动转给另一个。要么 full-mesh，要么显式 RR。
- **RR 要么全配、要么别配**：半成品 RR 留盲区。
- **配置一致性是 fleet 不变量**：加节点务必同步**所有**节点的 `routers`/`hosts`。
- **定位靠"最优路由来源分桶"**：`/routing/summary` 的 `peers` 计数最快；再用前缀级 diff（`via <protocol>`）钉死。
