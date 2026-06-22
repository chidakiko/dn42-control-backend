# 贡献指南

感谢你对 DN42 Control Backend 的兴趣!

## 开发环境

需要 Python 3.11+。

```bash
git clone https://github.com/chidakiko/dn42-control-backend.git
cd dn42-control-backend
python -m venv .venv
source .venv/bin/activate

# 首方包 + 应用 + 开发依赖(editable)
pip install \
  -e packages/dn42_common \
  -e packages/dn42_schemas \
  -e packages/dn42_runtime \
  -e packages/dn42_templates \
  -e apps/control-server \
  -e apps/node-agent \
  -e ".[dev]"
```

## 跑测试

```bash
python -m pytest                 # 全部(共享包 + control-server + node-agent)
python -m pytest tests/unit      # 仅共享包单测
python -m compileall apps packages tests
```

- 新功能 / 修复请带上对应测试。
- 改动模板或 `DesiredState` 结构会触发 `tests/unit/test_golden_rendered_hkg1.py` 黄金样本逐字节比对——按 `examples/rendered-hkg1/README.md` 里的命令重新生成快照后再提交。
- 协议模型是 Pydantic `StrictModel`(`extra=forbid`):增删字段要注意 agent↔server 的**锁步发布**顺序(加字段先升控制面,删字段先升 agent)。

## 代码风格

- `ruff`(行宽 100):`ruff check .` / `ruff format .`
- 公共 API 写 docstring;面向用户的文档放 `docs/`。

## 提交 PR

1. 从 `main` 切分支。
2. 提交信息用约定式前缀(`feat:` / `fix:` / `docs:` / `chore:` / `refactor:` …)。
3. 确保 `python -m pytest` 全绿。
4. 开 PR,说明动机与改动点(可套用 PR 模板)。

## 文档

架构与各子系统详见 [docs/](docs/):tutorial / operations / api / architecture / desired-state / node-agent / database / security / testing。
