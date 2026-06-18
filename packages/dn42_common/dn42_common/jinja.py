from __future__ import annotations

"""共享的 Jinja2 environment 工厂与 quoting filter。

所有下游渲染模块（`dn42_runtime.compose`、`dn42_templates.*`）都走这里
创建 Environment，以保证同一套语义：

* `trim_blocks` + `lstrip_blocks` + `keep_trailing_newline`——输出不多余空行、
  保留末尾换行（POSIX 文本文件习惯）。
* `StrictUndefined`——模板里访问未定义变量直接报错，避免隐式渲染为
  空字符串造成静默失败。
* 默认注入 `shell_quote` / `yaml_quote` filter，供脚本与 Compose 模板复用。
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined


def create_environment(template_dir: str | Path) -> Environment:
    """创建预设了 `shell_quote` / `yaml_quote` filter 与 StrictUndefined 的 Jinja2 Environment。"""

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
    )
    env.filters["shell_quote"] = shell_quote
    env.filters["yaml_quote"] = yaml_quote
    return env


def shell_quote(value: str) -> str:
    """把任意字符串包装为 POSIX shell 单引号安全形式。不依赖调用方预估 `value` 的转义状态。"""

    return "'" + value.replace("'", "'\\''") + "'"


def yaml_quote(value: object) -> str:
    """返回 YAML 双引号字符串，适合嵌入 compose 模板中任意表达式位置。"""

    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'