# 建立互联（Peering）

本文讲怎么建立对外 eBGP 对等连接、怎么组织内部 iBGP/OSPF、以及 route collector 喂送。eBGP 走 `peerings` + `bgp_sessions`；内部互联走 `internal_topology`（不是 `bgp_sessions`）。

## 概念区分

| 类型 | 怎么建 | 真相源 |
| --- | --- | --- |
| **eBGP 对外 peering** | Web「一键互联」向导 / `provision` / 逐资源 CRUD | `peerings` + `wg_interfaces` + `bgp_sessions` |
| **内部 iBGP / OSPF** | 编辑节点 `base_template.bird.internal_topology` | `internal_topology`（见 [monitoring-and-troubleshooting.md](monitoring-and-troubleshooting.md#内部互联ibgp--ospf--internal_topology)） |
| **route collector 喂送** | 加一条 `policy=route_collector` 的会话 | `bgp_sessions`（多跳、无隧道） |

`Peering` 是聚合根：一条逻辑互联聚合其下的 WireGuard 接口与 BGP 会话，可关联远端节点（`remote_node_id`）。模型见 [../reference/database.md](../reference/database.md#网络配置)，聚合根设计见 [../internals/control-server.md](../internals/control-server.md#peering-聚合根)。

## 用 Web 一键互联向导（推荐）

节点详情 → 概览页点 **⚡ 一键互联**，四步把建立一条对等连接所需配置填好，**无需手写 JSON**。提交时 peering + WireGuard 接口 + **首条** BGP 会话走 `provision` 端点**同事务**建立，其余会话用返回的 `peering_id` 补建。

1. **基本**：peer 名、对端 ASN、是否内部、标签 / 备注。
2. **WireGuard**：接口名、监听端口、MTU、本端地址、私钥引用（`secret://`）、对端公钥、endpoint、allowed_ips、keepalive、peer_routes。
3. **BGP**：加 0..N 条会话；三个预设 **+ IPv4 / + IPv6 链路本地 / + MP-BGP** 各带合理默认。纯传输 peer 可不加会话。
4. **确认**：摘要 + 只读预览，提交。

界面细节见 [web-ui.md](web-ui.md#一键互联向导添加-peer)。

## 用 API 建立

向导背后就是这些接口（见 [../reference/api.md](../reference/api.md#peering)）：

```bash
# 一次性建 peering + 接口 + 首条会话
curl -s -X POST "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/peerings/provision" \
  -H "Content-Type: application/json" -d @peer-foo.provision.json

# 之后补一条 v6 会话（用返回的 peering_id 关联）
curl -s -X POST "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/bgp-sessions" \
  -H "Content-Type: application/json" -d @peer-foo-v6-session.json
```

也可逐资源 CRUD：`POST /nodes/{id}/peerings`、`/interfaces`、`/bgp-sessions`，或对聚合根整体 PUT。字段见 [../reference/desired-state.md](../reference/desired-state.md)。`deploy/examples/` 与 `deploy/peer-*.provision.json` 有可参考的样例载荷。

## 地址要点

- eBGP-over-link-local：节点 LLA（`fe80::X`）是**一节点一个**、由 `NodeSpec.link_local` 单源派生到所有外部 eBGP WG 接口（见 [../reference/addressing-model.md](../reference/addressing-model.md#31-已解决的副本节点-lla-fe80x已收敛为派生)）。建外部 eBGP 接口时**不用**手填本端 LLA。
- 链路 p2p 地址（`/31` + `/127`）与 `source_address` 目前仍是各写一份的"副本"，注意两端一致。
- 入站 WireGuard 的监听端口必须落在节点 `wireguard_port_range` 内并对外发布，否则握不上手。

## route collector 喂送

route collector（如 Kioubit GRC）是把本节点路由喂给收集器、用于全局可视化的特殊会话——**多跳、无隧道**。加一条 `policy=route_collector` 的 `bgp_sessions` 即可（neighbor 指向收集器地址，无需 WireGuard 接口）。重连收集器可清掉其侧的陈旧前缀幽灵数据。会话字段见 [../reference/desired-state.md](../reference/desired-state.md#bgpsessionspec)。

## 验证

```bash
# 路由摘要（peers 分桶看最优来源）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/edge1/routing/summary"
# 节点本机
docker exec dn42-edge1-dn42-bird-router-1 birdc show protocols
```

会话起不来 / 缺路由的排错见 [monitoring-and-troubleshooting.md](monitoring-and-troubleshooting.md)。
