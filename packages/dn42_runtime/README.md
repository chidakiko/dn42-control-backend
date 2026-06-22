# dn42_runtime

`dn42_runtime` 提供 `RenderedFile` 路径安全校验、router Dockerfile 渲染、文件计划和原子写盘。容器编排不渲染文件，由结构化 runtime 数据直达 agent 的 Docker Engine API。

详细文档见 [../../docs/internals/shared-packages.md](../../docs/internals/shared-packages.md#dn42_runtime--文件产物与-dockerfile)。
