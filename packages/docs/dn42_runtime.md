# dn42_runtime

`dn42_runtime` 负责 runtime 层能力：`RenderedFile` 路径安全、router Dockerfile 渲染、文件计划和原子写盘。

它不调用 Docker Engine。真实部署由 Node Agent 完成——容器编排不渲染任何文件，容器定义以 `DesiredState.runtime` 的结构化数据直达 agent 的 Docker Engine API 后端。

## 文件结构

| 文件 | 内容 |
| --- | --- |
| `types.py` | `RenderedFile` 和路径安全校验 |
| `paths.py` | package data 模板目录定位 |
| `docker.py` | router Dockerfile 渲染 |
| `__init__.py` | `PlanAction`、`FilePlan`、`build_file_plan`、`write_rendered_files` |
| `config-docker/router/Dockerfile.j2` | router Dockerfile 模板 |

## RenderedFile

```python
RenderedFile(path="bird/bird.conf", content="...")
```

路径规则：

| 规则 | 目的 |
| --- | --- |
| 必须是字符串 | 避免非路径对象 |
| 不能为空 | 避免写到目录本身 |
| 不能含 NUL | 避免底层 API 截断 |
| 不能是绝对路径 | 防止写出目标目录 |
| 不能含 Windows 盘符 | 防止 `C:\...` |
| 不能含 `..` | 防止目录逃逸 |

## Docker 构建产物渲染

公开函数：

```python
create_config_docker_environment(template_dir=None)
render_router_dockerfile(dockerfile=None, env=None)
```

`render_router_dockerfile` 生成 router Dockerfile **内容**（字符串）——它不
属于渲染落盘产物：agent 把它经 Engine API ``fileobj`` 在内存中直接构建。
容器的运行参数（网络、端口、挂载、依赖、labels、sysctls……）同样不渲染
成文件——它们由 `dn42_schemas` 的解析函数（`resolve_service_*` /
`container_labels`）在 agent 的 Docker API 后端内直接消费。容器身份用
`dn42.config_hash` label（见 `dn42_common.service_config_hash`）。

## 文件计划

```python
build_file_plan(rendered_files, rendered_dir=None) -> FilePlan
```

计划动作：

| action | 含义 |
| --- | --- |
| `create` | 渲染结果存在，磁盘文件不存在 |
| `update` | 两边都存在但 SHA-256 不同 |
| `noop` | 两边都存在且 SHA-256 相同 |
| `delete` | 磁盘存在但渲染结果中不存在 |

`FilePlan.summary` 使用 `dn42_schemas.PlanSummary`。

## 原子写盘

```python
write_rendered_files(rendered_files, rendered_dir)
```

每个文件的写入步骤：

```mermaid
flowchart LR
    mkdir[创建父目录]
    tmp[写入同目录临时文件]
    replace[os.replace 原子替换]
    cleanup[清理临时文件]

    mkdir --> tmp --> replace --> cleanup
```

读者要么看到旧文件，要么看到完整新文件，不会看到半段写入内容。

## 设计边界

| 负责 | 不负责 |
| --- | --- |
| Docker 构建产物模板渲染 | BIRD/WireGuard/CoreDNS 业务模板 |
| 安全路径校验 | Docker Engine 调用 |
| 文件计划 | 容器创建和删除（容器编排是数据驱动，不渲染文件） |
| 原子写盘 | 采集 runtime 状态 |
