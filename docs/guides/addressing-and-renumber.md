# 寻址与 renumber

本文是 fleet 的**具体地址分配**与 **renumber 操作手册**。地址的概念模型（源/派生/副本）见 [../reference/addressing-model.md](../reference/addressing-model.md)。

> 本文以生产 fleet `AS4242420000 / example.dn42`（`172.20.0.0/26` + `fdce:1111:2222::/48`）为例。核心原则：**单播身份地址（节点 loopback）与任播共享地址（anycast 服务）严格分区、互不重叠**。

## 为什么要分区

历史分配把节点单播 loopback 和 anycast 服务段混在一起：hkg1=`.62`、pvg2=`.59` 落在 anycast `.56/29` 内。注册表里 `.56/29` 已登记为 anycast 双 origin 路由，对 `.59`/`.62` 的访问会被 anycast 路由到错误节点。解决办法：硬分区单播 `/27` vs 任播段。

## IPv4 分区：`172.20.0.0/26`

| 子段 | 范围 | 用途 |
|---|---|---|
| `172.20.0.0/27` | .0–.31 | **单播 / 节点专属** |
| └ `.0/28` | .0–.15 | 节点身份 loopback（`/32`，仅 IGP 内分发） |
| └ `.16/28` | .16–.31 | 预留（每节点单播服务 / 管理） |
| `172.20.0.32/27` | .32–.63 | **任播 / 共享服务** |
| └ `.56/29` | .56–.63 | **anycast 服务**（已登记路由，双 origin） |

## IPv6 分区：`fdce:1111:2222::/48`

| 子段（/64） | 用途 |
|---|---|
| `…:9500::/64` | **节点身份 loopback**（单播）：`::1`–`::N` |
| `…:56::/64` | **anycast 服务**（已登记）：`:56::53` = DNS |

> v4/v6 助记对齐：节点编号 `N` → v4 `.N` + v6 `:9500::N`；anycast v4 `.56/29` ↔ v6 `:56::/64`（数字 `56` 对齐）。

## 节点身份 loopback 分配（示例 fleet）

| 节点 | 编号 | v4 | v6 |
|---|---|---|---|
| hkg1 | 1 | 172.20.0.1 | `…:9500::1` |
| hkg2 | 2 | 172.20.0.2 | `…:9500::2` |
| pvg2 | 3 | 172.20.0.3 | `…:9500::3` |
| can2 | 4 | 172.20.0.4 | `…:9500::4` |

## Anycast 服务分配

| 服务 | v4（`.56/29`） | v6（`:56::/64`） |
|---|---|---|
| DNS（ns1.example.dn42） | 172.20.0.57 | `…:56::53` |

anycast 服务地址来源：DNS 组的 `bind_addresses`，agent 据此派生受管 `dns-anycast` dummy 接口。新增 anycast 服务 = 在组里加 bind 地址（落在 `.56/29` / `:56::/64`），见 [dns-and-anycast.md](dns-and-anycast.md)。

## renumber 一个节点的同步点

renumber 节点 loopback 会重置 router_id ⇒ **iBGP/OSPF 会重建、eBGP 受 router_id 变更影响**，属有中断操作，逐节点低峰执行。一处真值（节点 loopback）牵动多处，**漏一处就坏**：

1. **节点身份**：`PATCH /nodes/<n>` 改 `router_id` / `loopback_ipv4` / `loopback_ipv6`。
2. **所有节点**的 `base_template.bird.internal_topology.hosts[*].ownip/ownip6` 同步（fleet 不变量：各节点 internal_topology 的 routers+hosts 必须一致，否则缺路由，见 [monitoring-and-troubleshooting.md](monitoring-and-troubleshooting.md#不变量最重要)）。
3. **eBGP `source_address`**：非 LLA 邻居需同步并通知对端。
4. **DNS 记录**：正向 A/AAAA + 反向 PTR（正/反向 zone）→ 新 loopback。
5. materialize + 通知节点重拉。

> 第 2、4 点正是当前仍是"副本"的危险点（见 [../reference/addressing-model.md](../reference/addressing-model.md#副本清单renumber-危险点速查)）；漏改不会立刻报错，而是隐蔽缺路由 / 解析错误。

## 迁移脚本

这些脚本封装了上面的多点同步（环境变量 `DN42_CP` + `DN42_ADMIN_TOKEN`，默认 dry-run，写 `.json` 备份）。完整参数见 [../reference/cli-and-scripts.md](../reference/cli-and-scripts.md)。

| 脚本 | 作用 |
| --- | --- |
| `deploy/renumber_loopbacks.py` | 一把改节点 loopback + 同步所有节点 `internal_topology.hosts` + DNS 记录 |
| `deploy/unify_internal_topology.py` | 把各节点 `internal_topology` 统一为全节点 full-mesh（⚠️ 历史版本硬编码旧地址，renumber 后须先更新再跑） |
| `deploy/dns_anycast_lo_cleanup.py` | anycast 地址从 `dn42-lo` 迁到 `dns-anycast` dummy |
| `scripts/tools/create_rdns_26.py` | 批量创建 `/26` 反向 DNS |
| `scripts/tools/backfill_node_lla.py` | 剥接口 addresses 里的 LLA 副本（LLA 已收敛为派生后） |

## 迁移后验证

```bash
# iBGP/OSPF 邻接恢复，best 前缀数与迁移前一致
docker exec dn42-<node>-dn42-bird-router-1 birdc show protocols
# DNS 正反向正确
dig @172.20.0.57 example.dn42 SOA
dig @172.20.0.57 -x 172.20.0.1
# anycast .56/29 内不再出现任何节点单播 /32（.57 DNS 之外仅预留）
curl -s "http://127.0.0.1:8000/api/v1/admin/nodes/<node>/routing/summary"
```

## 不在分区内

内部 underlay（`10.254.42.0/24`，router netns 容器互联）与 WireGuard 链路本地址（`fe80::/64` 等）属节点内部 / 链路层，不进公网 `/26`、`/48` 身份/任播分区（见 [../reference/addressing-model.md](../reference/addressing-model.md#第-5-层runtime--underlay容器层--影响半径节点内部)）。
