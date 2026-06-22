# web

DN42 控制面的管理 Web UI：SvelteKit + `adapter-static` 的纯静态 SPA，独立托管，经 CORS + Bearer（token 存 localStorage）直连 Control Server 的 Admin API。

文档（单一事实源在 `docs/`）：

- 操作指南（登录、仪表盘、节点详情各页签、一键互联向导、审批、provision、审计）：[../../docs/guides/web-ui.md](../../docs/guides/web-ui.md)
- 托管与 CORS：[../../docs/guides/deployment.md](../../docs/guides/deployment.md#web-ui-托管)

开发与构建：

```bash
npm install
npm run dev          # http://127.0.0.1:5173
npm run build        # 产物在 build/（静态站，用 nginx/Caddy 托管）
```

控制面需把本 UI 的来源加入 `DN42_CONTROL_CORS_ORIGINS`。
