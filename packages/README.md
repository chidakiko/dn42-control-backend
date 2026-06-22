# packages

`packages/` 是 Control Server 与 Node Agent 共享的一方基础库。各包职责、依赖方向、关键模型 / 校验器 / 模板的完整说明见 **[../docs/internals/shared-packages.md](../docs/internals/shared-packages.md)**。

| 包 | 职责 |
| --- | --- |
| `dn42_common` | 校验器、命名、label、community、Jinja、crypto、canonical 序列化 |
| `dn42_schemas` | Pydantic 协议模型（`DesiredState`、Agent 协议），字段见 [../docs/reference/desired-state.md](../docs/reference/desired-state.md) |
| `dn42_templates` | BIRD / WireGuard / CoreDNS / 脚本渲染 |
| `dn42_runtime` | `RenderedFile`、写盘计划、router Dockerfile 渲染 |

依赖方向（箭头 = import）：`schemas`/`runtime`/`templates` → `common`；`templates` → `schemas`/`runtime`/`common`。底层包不反向 import 上层。
