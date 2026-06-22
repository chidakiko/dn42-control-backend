# 贡献指南

完整贡献指南（开发环境、测试分层、golden 回归、代码风格、PR 流程、文档维护约定）见 **[docs/contributing.md](docs/contributing.md)**。

速览：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
python -m pytest
```

- 提交信息用约定式前缀（`feat:` / `fix:` / `docs:` / `chore:` / `refactor:`）。
- 改模板 / `DesiredState` 结构会触发 golden 逐字节回归，按 [docs/contributing.md](docs/contributing.md#golden-渲染回归) 刷新快照。
- 协议模型是 `StrictModel`（`extra=forbid`），增删字段注意 agent↔server 锁步发布顺序。
