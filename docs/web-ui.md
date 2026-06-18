# 管理 Web UI 操作指南

面向**用浏览器操作控制面**的运维人员。Web UI 是一个独立的静态 SvelteKit 应用（`apps/web`），
不含任何服务端逻辑——它把 admin token 存在浏览器 localStorage，直接以
`Authorization: Bearer` 调控制面的 Admin API。所以**一份静态构建可以指向任意 fleet**，
在登录页填不同的控制面地址即可。

> 接口细节（请求/响应字段）只在 [api.md](api.md) 维护；本文只讲「界面上怎么点」。

## 启动与访问

```bash
cd dn42-control-backend/apps/web
npm install        # 首次
npm run dev        # 开发模式，默认 http://127.0.0.1:5173
# 或构建静态站托管：
npm run build      # 产物在 apps/web/build/（adapter-static）
```

控制面需开启 CORS 允许该来源（`DN42_CONTROL_CORS_ORIGINS`，见 [configuration.md](configuration.md)）。

## 登录

![登录](images/wui-login.png)

填两项：**控制面地址**（如 `http://127.0.0.1:8000` 或线上 `http://<host>:8000`）与 **admin token**。
token 仅存浏览器本地、直发控制面。401 即 token 失效，会自动登出回此页。右下角可随时切换
**语言（中/英）** 和 **主题**。

## 仪表盘（总览）

![总览](images/wui-dashboard.png)

fleet 级健康与路由概览：节点在线/掉线、各节点世代、路由规模等。卡片可下钻到对应节点。

## 节点列表

![节点列表](images/wui-nodes.png)

所有节点一览（ASN、loopback、世代、生命周期、健康）。点任意节点进详情。

## 节点详情

顶部是多页签：**概览 / Peering / 接口 / BGP 会话 / 内部互联 / 路由表 / DNS 区域 / 版本历史 /
状态事件 / 期望状态 / 令牌**。支持 `?tab=<id>` 深链。

### 概览

![节点概览](images/wui-node-overview.png)

节点身份字段 + 危险操作（退役 / 删除）。右上角操作区：
- **⚡ 一键互联** —— 打开「添加 peer」向导（见下节），是新增对等连接的**唯一入口**。
- **通知更新 / 请求快照** —— 手动给 agent 推事件（节点掉线时禁用）。
- **编辑** —— 改节点身份与 `base_template`（含 `bird.internal_topology`，见 [internal-interconnect.md](internal-interconnect.md)）。

### Peering / 接口 / BGP 会话

![接口](images/wui-node-interfaces.png)

「接口」「BGP 会话」「DNS 区域」是通用的 spec 资源页：列表 + 直接编辑 `spec`（JSON）。
「Peering」页是对等关系的元信息列表（编辑/删除），**新增对等连接走概览的「一键互联」向导**，
不在此页手填。

### 内部互联（iBGP / OSPF）

![内部互联](images/wui-node-internal.png)

iBGP / OSPF 不是 BGP 会话记录，而由 `internal_topology` 自动合成，所以单独成页：展示 iBGP 对端、
OSPF 协议与邻接，以及来自路由快照的 liveness（有最优路由即会话已起）。排错与不变量见
[internal-interconnect.md](internal-interconnect.md)。

## 一键互联向导（添加 peer）

概览页点 **⚡ 一键互联**，分四步把建立一条对等连接所需的全部配置填好——**无需手写 JSON**。
提交时：peering + WireGuard 接口 + **首条** BGP 会话走 `provision` 端点**同事务**建立，其余会话
（如 v6 伴随）随后用返回的 `peering_id` 补建。

**① 基本** —— peer 名、对端 ASN、是否内部、标签/备注。

![向导-基本](images/wui-wizard-1.png)

**② WireGuard** —— 接口名（默认按 peer 名带入）、监听端口、MTU、本端地址、私钥引用、对端公钥、
endpoint、allowed_ips、keepalive、peer_routes。地址类用「每行一个」文本框。

![向导-WireGuard](images/wui-wizard-2.png)

**③ BGP** —— 可加 **0..N 条**会话；三个一键预设 **+ IPv4 / + IPv6 链路本地 / + MP-BGP** 各自带好
合理默认（地址族、neighbor、extended next hop）。纯传输 peer 可不加会话。

![向导-BGP](images/wui-wizard-3.png)

**④ 确认** —— 文字摘要 + 接口/会话的只读预览，点「创建对等连接」提交。

![向导-确认](images/wui-wizard-4.png)

## 注册审批

![注册审批](images/wui-registrations.png)

新节点 agent 用 enrollment token 注册后落到「待审批」，在此**批准/拒绝**（per-node 准入闸门，见
[security.md](security.md)）。

## 注册令牌（enrollment token）

![注册令牌](images/wui-enrollment.png)

签发 / 吊销 enrollment token（节点首次接入用），token 明文只在创建时显示一次。

## 导入下发（provision）

![导入下发](images/wui-provision.png)

贴一份完整 `DesiredState` JSON 一次性建/覆盖整节点（幂等），适合离线渲染好的状态导入或批量灌库。
字段规则见 [desired-state.md](desired-state.md)。

## 审计日志

![审计](images/wui-audit.png)

admin 写操作的审计流水（谁、何时、改了什么）。

## 截图怎么再生成

本文截图由 `apps/web/scripts/doc-shots.mjs`（playwright-core + msedge headless）生成，输出到
`docs/images/`。需要本地起 seeded 控制面 + vite，再跑脚本：

```bash
# 终端 1：seeded 控制面
export DN42_CONTROL_ADMIN_TOKEN=dev-admin-token
export DN42_CONTROL_SEED_BOOTSTRAP_NODE=1
export DN42_CONTROL_CORS_ORIGINS=*
export DN42_CONTROL_DATABASE_URL=sqlite+aiosqlite:///./docshots.db
.venv/bin/python -m uvicorn app.main:app --app-dir apps/control-server --host 127.0.0.1 --port 8001
# 终端 2：web
cd apps/web; npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
# 终端 3：截图（端口要与脚本顶部 BASE/API 一致）
cd apps/web; node scripts/doc-shots.mjs
```
