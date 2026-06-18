# 内部互联：iBGP / OSPF / internal_topology

本文讲同一个 AS 内多个节点之间怎么互联（iBGP 交换路由 + OSPF 提供 loopback 可达），
以及一条**容易被破坏、破坏后很隐蔽**的不变量。文末是一例真实事故的 postmortem。

> eBGP 对端（对外 peering）走 `bgp_sessions`，由「一键互联」向导/`provision` 建立，见
> [web-ui.md](web-ui.md) / [api.md](api.md)。**内部互联不走 `bgp_sessions`**，而由下面的
> `internal_topology` 合成。

## 是什么

`DesiredState.bird.internal_topology`（schema `InternalTopologySpec`，见
[desired-state.md](desired-state.md)）驱动模板层 `ibgp.conf.j2` + `ospf.conf.j2`
（`packages/dn42_templates/dn42_templates/config-bird2/`）生成 iBGP 与 OSPF 配置：

| 字段 | 作用 |
| --- | --- |
| `routers` | 参与内部路由域的节点名列表。`full_mesh_ibgp=true` 时，节点对 `routers` 里**除自己外**的每个节点建一条 iBGP 会话。 |
| `hosts` | 节点名 → `{ownip, ownip6, ibgp_rr_upstreams}`。`ownip/ownip6` 是该节点 loopback（iBGP next-hop / neighbor 地址）。`ibgp_rr_upstreams` 非空表示本节点是某 RR 的 client。 |
| `igp_adjacencies` | **本节点自己的**物理内部链路：`{node, interface, cost, iface_type}`。OSPF 邻接只在这些真实接口（内部 WG 隧道）上形成。 |
| `full_mesh_ibgp` | 是否在 `routers` 之间建 full-mesh iBGP。 |
| `ospf_v2` / `ospf_v3` | 是否生成 OSPFv2 / v3。 |
| `private_nodes` | 仅内部可见、不一定对外宣告的节点。 |

iBGP 会话跑在 **loopback** 上（next-hop-self），不需要两节点物理直连——但**需要 OSPF 把对端
loopback 传到本节点**，否则会话因 neighbor 不可达起不来。所以 OSPF（解析 loopback）是 iBGP 的前提。

UI 上的「内部互联」页（节点详情）展示合成出来的 iBGP 对端 / OSPF 邻接 + liveness：

![内部互联页](images/wui-node-internal.png)

## 不变量（最重要）

> **同一个 AS 内，所有节点的 `internal_topology.routers` + `hosts` 必须是同一份完整集合。**

每个节点**只**从自己这份 `internal_topology` 生成 iBGP/OSPF。它不会自动发现别的节点。
所以如果各节点的 `routers`/`hosts` 不一致，就会各建各的「部分网格」，导致某些节点缺路由——
而且因为 eBGP 不受影响、节点本身 health 正常，这种缺失很隐蔽。

`igp_adjacencies` 是**例外**：它是每节点各自的物理链路，本就应该不同（只列本节点直连的内部 WG 接口）。

### 拓扑选择
- **full-mesh iBGP**（推荐，节点少时）：所有节点 `routers` 含全员、`full_mesh_ibgp=true`、`hosts`
  含全员 loopback、`ibgp_rr_upstreams` 全空。N 个节点 = N·(N-1)/2 条会话。
- **route reflector**：选一个节点当 RR，其余节点在自己的 `hosts[self].ibgp_rr_upstreams` 里指向 RR。
  **要么全 full-mesh、要么所有节点对 RR 关系一致**——别留半成品（见 postmortem）。

## 加内部节点 checklist

加一个新节点进内网时，**必须**：
1. 给新节点和某个已有节点之间建内部 WG 隧道（p2p /31 + /127 + `fe80::` link-local），双方各加一条
   `igp_adjacencies` 指向对方接口。
2. **把新节点加进所有已有节点**的 `internal_topology.routers` 和 `hosts`（loopback），并把全员补进
   新节点自己的 `routers`/`hosts`。漏掉任何一个节点，它（或新节点）就会缺路由。
3. 下发后到「内部互联」页或 `/routing/summary` 确认：新节点的最优路由里出现了 iBGP 来源、各节点
   `route_count` 一致。

> 可参考 `deploy/unify_internal_topology.py`：它把全 fleet 的 `routers`/`hosts` 统一成同一份
> full-mesh，并保留各节点自己的 `igp_adjacencies`。

---

## Postmortem：pvg2 比同 AS 节点少 12–15 条路由（2026-06-16）

### 现象
4 节点同属 AS4242420000。控制面路由观测显示：

| 节点 | route_count | 备注 |
| --- | --- | --- |
| can2 / hkg1 / hkg2 | 2216 | 三家**逐位**一致（连前缀长度分布都相同） |
| pvg2 | 2201 | 少 ~15 条 |

诡异点：**没有任何 eBGP 对端、纯靠 iBGP 学习的 hkg2 反而是满的 2216，而有自己 eBGP 的 pvg2 却缺**。

### 根因
物理内网是一条链：`pvg2 —— can2 —— hkg2 —— hkg1`（只有相邻两节点有内部 WG 隧道）。
而四节点的 `internal_topology` **各不一致**：

| 节点 | 自己的 routers | 实际 iBGP |
| --- | --- | --- |
| pvg2 | `{pvg2, can2}` | 只跟 can2 建——**根本不认识 hkg1/hkg2** |
| can2 | `{pvg2, can2, hkg2}` | 跟 pvg2、hkg2 建，不认识 hkg1 |
| hkg2 | `{hkg2}` + `can2/hkg1` 设成它的 RR client | 半成品 route reflector |
| hkg1 | `{hkg1, hkg2}` | 只跟 hkg2 建 |

hkg2 作为 RR 把 hkg1↔can2 的路由互相反射，所以 can2/hkg1/hkg2 都满。但 **can2 不是 RR**：它从
hkg2(iBGP) 学到的 hkg1 路由**不会再转发给 pvg2**——这是 iBGP 的水平分割规则（从一个 iBGP 邻居学到的
路由不再通告给另一个 iBGP 邻居，除非本节点是 RR）。pvg2 只挂在 can2 上，于是只拿到 can2 **自己 eBGP**
的路由，**永远拿不到只经 hkg1 反射出来的那批前缀**。

pvg2 缺的恰好不多（~15 条）的原因：pvg2 自己的一个 eBGP 对端是近全表，已覆盖绝大多数前缀，只有
hkg1 那几个小众对端独有的十几条它的对端不带。

### 诊断手法（全程只读控制面 API）
1. **`/routing/summary` 的 `peers` 分桶**看最优路由来源——pvg2 当时 iBGP 来源 **0 条**、全是自己 eBGP，
   一眼看出它没在用任何 iBGP 路由。
2. **`/nodes/{id}/internal-topology`** 看各节点 iBGP/OSPF 邻接范围——直接暴露 pvg2 只有 can2、
   四节点 routers/hosts 不一致。
3. **`/routing/prefixes?limit=1000&offset=` 翻页**拉各节点 primary 前缀集求差集——确认 pvg2 是 hkg2 的
   严格子集，且缺的全部 `via ibgp_hkg1`，钉死结论。

### 修复
把四节点统一成 full-mesh：所有节点 `routers=[pvg2,can2,hkg2,hkg1]`、`hosts` 含四个 loopback、
`private_nodes=[]`、丢掉 hkg2 的半成品 RR、`full_mesh_ibgp=true`，各自 `igp_adjacencies` 不动。
脚本 `deploy/unify_internal_topology.py`（`deploy` / `--verify` / `--rollback`，token 走环境变量
`DN42_ADMIN_TOKEN`，备份落 `it_backup.json`）给每个节点 PATCH `base_template.bird.internal_topology`。

结果：**30 秒内 pvg2 从 2201/2203 收敛到 2216**，四节点逐条一致，pvg2 新增 `ibgp_hkg1` 最优来源
~1136 条。**eBGP 全程不受影响**（不同协议）。OSPF 本来就是好的——新的 pvg2↔hkg1/hkg2 iBGP 会话能起，
说明 loopback 经 OSPF 沿链可达（之前担心的「OSPF 只传一跳」不成立）。

### 经验教训
- **iBGP 不中继**：从一个 iBGP 邻居学到的路由不会自动转给另一个邻居。要么 full-mesh，要么显式 RR。
- **RR 要么全配、要么别配**：半成品 RR（只有部分节点是 client）会留下盲区。
- **配置一致性是 fleet 不变量**：`internal_topology.routers/hosts` 不一致 = 隐蔽缺路由。加节点时
  务必同步**所有**节点。
- **定位靠「最优路由来源分桶」**：`/routing/summary` 的 `peers` 计数是判断「某节点在不在用 iBGP/某条
  会话」的最快信号；再用前缀级 diff（`via <protocol>`）钉死缺哪些、从哪来。
