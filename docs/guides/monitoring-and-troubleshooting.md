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
# 历史事件（排错常用；kind 可选 snapshot / report / apply / reresolve）
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

**诊断**：
1. `/routing/summary` 的 `peers` 分桶——pvg2 iBGP 来源 0 条，一眼看出没用任何 iBGP 路由。
2. `/nodes/{id}/internal-topology`——暴露 pvg2 只有 can2、四节点 routers/hosts 不一致。
3. `/routing/prefixes` 翻页求差集——确认 pvg2 是 hkg2 严格子集，缺的全是 `via ibgp_hkg1`。

**修复**：四节点统一 full-mesh（`routers=[pvg2,can2,hkg2,hkg1]`、`hosts` 含四 loopback、丢掉半成品 RR、`full_mesh_ibgp=true`，`igp_adjacencies` 不动），脚本 `deploy/unify_internal_topology.py`。结果 30 秒内 pvg2 收敛到 2216，eBGP 全程不受影响。

**教训**：
- **iBGP 不中继**：从一个 iBGP 邻居学到的路由不会自动转给另一个。要么 full-mesh，要么显式 RR。
- **RR 要么全配、要么别配**：半成品 RR 留盲区。
- **配置一致性是 fleet 不变量**：加节点务必同步**所有**节点的 `routers`/`hosts`。
- **定位靠"最优路由来源分桶"**：`/routing/summary` 的 `peers` 计数最快；再用前缀级 diff（`via <protocol>`）钉死。

---

## Postmortem：多宿主对端去/回程不对称——local_pref 尝试与回退（2026-06-23）

**现象**：外部 peer AS4242421111 traceroute 到 tpe1 延迟异常高（到 tpe1 ~108ms，且在 hkg2 那一跳单跳 +45ms）。但底层 WG 链路实测都很快（`can2↔hkg2` 仅 4.7ms），逐跳增量与链路 RTT 对不上。

**根因**：AS4242421111 **双宿主**——节点 `peer-a` 对 pvg2 对等（pvg2↔peer-a = 24ms 快口），节点 `peer-b` 对 hkg1 对等（hkg1↔peer-b = 97ms 慢口）。去程 peer-a 从 pvg2 进、走 `pvg2→can2→hkg2→tpe1`（快）；但**回程**各节点按 BGP 选最优时，两条入口的 `local_pref`（都 100）、AS-path（都 1 跳）、MED（冷土豆都 50）全平，落到 **IGP 距离**——tpe1 选了 IGP 最近的 hkg1（下一跳 cost 100 < 经 pvg2 的 300），回程走 `tpe1→hkg1→peer-b→peer-a`（97ms 慢口）。去/回程进出的是同一个 AS 的**两条不同 peering** = 非对称，慢的全在回程。BGP 只看 IGP 距离、不看真实延迟。

**诊断**：
1. 逐跳 RTT 增量 vs 底层链路实测对不上（hkg2 跳 +45ms，可 `can2↔hkg2` 仅 4.7ms）→ 怀疑回程。
2. pvg2 自身 traceroute 到 tpe1 干净对称（50ms）→ 证明前向无问题，问题在回程。
3. **三点抓包对齐时间戳**（入口 pvg2 / 目的 tpe1 / 疑似回程出口 hkg1）：回包 `tpe1 wg-hkg1 Out` → `hkg1 as4242421111 Out`，坐实回程经 hkg1/peer-b。
4. 量化两入口：`pvg2↔peer-a` 24ms vs `hkg1↔peer-b` 97ms（差 4 倍）。

**尝试与回退**：先做了会话级 `BgpSessionSpec.local_pref`，给 pvg2 的 peer-a 会话设 `local_pref=200`（local_pref 优先级高于 IGP 距离，使全 fleet 优先 pvg2 那条 24ms 快口）。peer-a 侧确实改善（tpe1→peer-a 110→70ms）。但暴露**两个问题**，最终**回退**：

1. **共享前缀无法分而治之**：AS4242421111 双宿主——peer-a（`.162`@pvg2）与 peer-b（`.166`@hkg1）**共用同一个 `172.20.0.160/27`、无更具体路由**。local_pref 作用于整段，peer-b 被殃及：`tpe1→peer-b` 从 ~19ms（直连 hkg1 仅 5ms）暴涨到 88ms。peer-b 离 hkg1 更近，损失（68ms）> peer-a 的收益（40ms）——只测了 peer-a 没测 peer-b，误判成"零回退"。

2. **session 级 local_pref 在 transit peer 上是 fleet 级爆炸半径**：peer-a 是**透传 peer**——这条 session 导入 **2290 个前缀**，其中 AS4242421111 自己只占 **3 个**，其余 ~2287 是透传别家。local_pref 加在 session 的 import 上，会无差别把**整条 session 的 2290 个前缀**全抬到 200，而 200 压倒 AS-path/MED/IGP 距离 → 全 fleet 这两千多个前缀的出口被强行灌向 `pvg2↔peer-a` 单点。看到的 peer-b 绕路只是**冰山一角**，背后上千前缀被悄悄改道（`show route protocol <sess> count`：peer-a 凭实力本该只拿 472 条）。

故**回退 local_pref，恢复默认 hot-potato**（按 IGP 距离各走最近入口）——它对"双宿主 + 共享前缀"其实是 optimal-on-average：`tpe1→peer-b` 回到 19ms、`tpe1→peer-a` 回到 110ms（这 110ms 是 peer-b↔peer-a 间 ~90ms 的 AS 内部延迟 + 共享前缀，**本侧 fleet 无法消除**）。根治只能靠对端宣告 per-PoP 更具体路由。

**教训**：
- **共享前缀不可分**：双宿主对端从两 PoP 宣告同一聚合前缀时，BGP 按前缀选路、社区 / local_pref 只影响"哪条路径胜出"但仍是整段一条出口，无法把 `.162` / `.166` 分流。要分别选优只能靠对端发**更具体路由**（不在本侧控制范围内）。
- **session 级 local_pref 是 per-session 的粗粒度开关、不是 per-peer 微调**：它无差别作用于该 session 的**所有**路由；对 transit peer 等于劫持其整个 transit cone，爆炸半径 = 该 session 的导入前缀数（本例 2290）。真要细调得 **per-prefix**（在过滤器里按前缀 / 社区匹配再设），而非整条 session。
- **改完必须量化"另一侧" + 查爆炸半径**：只测了一端（peer-a）、漏了另一端（peer-b）；只想着一个前缀、没数 session 带了多少路由。多宿主 / transit 对端务必**两侧都测**、并 `show route protocol <sess> count` 看影响面。
- **hot-potato 往往就是双宿主的最优解**：默认按 IGP 距离各走最近入口、平均最优；强行全局指向单一入口，helped 远端 hurt 近端。
- **traceroute 每跳 RTT 含回程**：增量与底层链路 RTT 对不上就是回程不对称信号；**三点抓包对齐**（入口 / 目的 / 疑似回程出口）是坐实手段。
- **`local_pref` 功能保留**：适合 peer-only、且确实想整条会话抬权的场景。BIRD 落地用 `import filter { bgp_local_pref = N; dn42_import_filter(...); }`——`default bgp_local_pref` **不是合法 BIRD 语法**（会 syntax error、配置加载失败）。

---

## Postmortem：跨境专线节点——名字按出口 POP、机器在大陆，WG 拨号方向（2026-06-23）

**现象**：pvg2↔tyo2 内网 underlay 实测 **198ms / 20% 丢包**（pvg2↔can2 同期 30ms/0%，pvg2 自身链路正常）。同期 tyo1↔tyo2 = 27ms。

**根因**：`tyo2` / `hkg2` 是**不对称跨境专线节点**，名字按**出口 POP** 取（tyo2→东京、hkg2→香港），但**机器物理在大陆**。各有两个公网地址：
- **境内入口**（= 大陆主机本身）：tyo2 `198.51.100.10`、hkg2 `198.51.100.20`。境内节点从此拨入为 5ms 级短路。
- **境外出口**：tyo2 `203.0.113.10`（东京）。节点拨出境外时使用；从大陆 ICMP 直连此 IP 不通（100% 丢）。

原配置为 **tyo2 主动拨 pvg2**，WG 从 tyo2 的**境外出口**(203.0.113.10)发出，绕经东京再回上海，故 198ms/20%。tyo1↔tyo2 = 27ms 则属正常：tyo2 在大陆、tyo1 在东京，27ms 即大陆↔东京一跳跨境时延，与物理距离相符。

**WG 拨号方向规则**（违反即 198ms）：
- **境内节点（pvg2/can2）主动拨** tyo2/hkg2 的**境内入口**（短路径）；
- **tyo2/hkg2 主动拨出**到境外节点（tyo1/hkg1/tpe1），走各自境外出口侧。

**修复**：翻转 tyo2↔pvg2——`PATCH` tyo2 `wg-pvg2` 改监听（endpoint=null）、pvg2 `wg-tyo2` 改拨 `198.51.100.10:51821`（境内入口）。underlay 降至 **5.5ms/0%**。can2↔hkg2 原已是 can2 拨 hkg2 入口（4.6ms），无需调整。

**命名约定 + 标签**：跨境专线节点**按出口 POP 命名**（显式约定，避免改名牵动全 fleet `internal_topology`/对端 wg 接口/容器名）。物理位置记入节点 `labels`：`role=crossborder-relay`、`physical_region=cn-east|cn-south`、`egress_pop`、`ingress_ip`/`egress_ip`，用于区分"名取海外 POP、机器在大陆"。

**诊断**：在境内节点 debug-shell 分别 ping 对端 **loopback、境内入口 IP、境外出口 IP** 三者的 RTT/丢包——拨号方向正确（拨入口）应为 5ms 级；误拨出口则 ~200ms/高丢包。`birdc show ospf neighbors` 查 underlay 对端 IP 可确认实际走向。

**教训**：
- **节点名 ≠ 物理位置**：跨境专线节点名取自出口 POP，机器在大陆。排障应先查 `labels.physical_region`，不以节点名推断地理位置——大陆↔东京的 27ms 属正常时延，非故障。
- **拨号方向决定 WG 走入口还是出口**：拨错一侧即跨境绕路 198ms。规则为境内拨入口、跨境节点拨出。

---

# 路由调优三件套（社区 / 选路）

把"按前缀精调"和"社区"真正用起来的三个旋钮，从粗到细：

| 旋钮 | 位置 | 作用 | 注意 |
| --- | --- | --- | --- |
| `BgpSessionSpec.link_latency`（1-9） | 会话 | 给该会话学到的路由打 DN42 `(64511, 档)` **延迟社区**（端到端取最差档），propagate 到全网 / looking-glass / 对端可见 | ⚠️ **仅可见性/信令**。本 fleet **不**据此设 local_pref——latency 社区只含 eBGP 链路、不含 fleet 内部跳数，据它选路会让节点弃近就远；选路仍交给默认 hot-potato（IGP 距离已算进内部跳数） |
| `Bird2ConfigSpec.cold_potato_med`（默认 50） | 节点 | 同大区 region 社区的路由 import 时设此 MED（越低越优）→ 优先就近同区域路径 | 跨区域保持 100；改的是 MED（比 local_pref 温和，不越过 AS-path / eBGP-direct） |
| `Bird2ConfigSpec.route_local_pref`（`[{prefix, local_pref}]`） | 节点 | 对**精确匹配的单个前缀**设 `bgp_local_pref`——精细路由调优 | **只影响该前缀**、不波及 session 其余路由（避开 transit peer 的 fleet 级爆炸半径）；仅本节点导入侧、不导出；要全 fleet 优先某入口只需在该入口节点配一条 |

**`route_local_pref` 是"调一个前缀的选路"的正解**（替代上面会话级 `local_pref` 的粗粒度做法）。例：让 fleet 对 `172.20.0.160/27` 优先走 pvg2 入口——只在 pvg2 的 `base_template.bird.route_local_pref` 加 `{"prefix": "172.20.0.160/27", "local_pref": 200}`，经 iBGP 传播即可，且**不碰**该 session 透传的其余前缀。

```bash
# 验证只动目标前缀、不波及透传：
docker exec dn42-pvg2-dn42-bird-router-1 birdc show route for 172.20.0.160/27 all   # local_pref=200
docker exec dn42-pvg2-dn42-bird-router-1 birdc show route protocol <sess> count        # 其余仍 100
```
