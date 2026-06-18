# rendered-hkg1 示例

这个目录保存 `dn42_schemas.testing.build_hkg1_example_state()` 通过 `dn42_templates.render_desired_state()` 生成的完整节点配置。

这是 `dn42_templates` 的黄金样本：`tests/unit/test_golden_rendered_hkg1.py` 会把每次渲染结果与本目录逐字节比对，所以**任何对模板或 desired-state 结构的改动都需要在合并前刷新这里的内容**（按下文“重新生成”）。

BIRD2 配置来自 `dn42_templates/config-bird2`。WireGuard、CoreDNS 和启动脚本分别来自 `config-wireguard`、`config-coredns` 和 `config-scripts`。容器编排与 router 镜像构建都不渲染文件：编排由结构化 runtime 数据直达 agent 的 Docker Engine API，Dockerfile 由 agent 按 `runtime.router_dockerfile` 在内存生成（`dn42_runtime.render_router_dockerfile`）。

## 重新生成

```bash
target="$PWD/examples/rendered-hkg1"
rm -rf "$target"
mkdir -p "$target"
python -c "from pathlib import Path; from dn42_schemas.testing import build_hkg1_example_state; from dn42_templates import render_desired_state; from dn42_runtime import write_rendered_files; out = Path('examples/rendered-hkg1'); write_rendered_files(render_desired_state(build_hkg1_example_state()), out)"
```

## 配置校验

```bash
python -c "from dn42_runtime import render_router_dockerfile; print(render_router_dockerfile())" | docker build -t dn42-bird2-verify:local --target bird-router -
docker run --rm -v "${PWD}/examples/rendered-hkg1/bird:/etc/bird:ro" dn42-bird2-verify:local bird -p -c /etc/bird/bird.conf
```

启动一个临时 BIRD daemon：

```bash
existing=$(docker ps -aq --filter "name=^dn42-bird2-rendered-check$")
if [ -n "$existing" ]; then docker rm -f dn42-bird2-rendered-check >/dev/null; fi
docker run -d --name dn42-bird2-rendered-check --cap-add NET_ADMIN -v "${PWD}/examples/rendered-hkg1/bird:/etc/bird:ro" dn42-bird2-verify:local sh -c "mkdir -p /run/bird /var/log/bird && bird -f -c /etc/bird/bird.conf"
docker exec dn42-bird2-rendered-check birdc show status
```
