from __future__ import annotations

"""runtime 层使用的简单值对象。"""

from dataclasses import dataclass
from pathlib import PurePosixPath


def _validate_relative_path(path: str) -> str:
    """校验渲染文件相对路径，防止路径穿越 / 绝对路径 / NUL。

    返回归一化后的 POSIX 风格相对路径字符串。
    """

    if not isinstance(path, str):
        raise TypeError(f"RenderedFile.path must be str, got {type(path).__name__}")
    if not path:
        raise ValueError("RenderedFile.path must not be empty")
    if "\x00" in path:
        raise ValueError("RenderedFile.path must not contain NUL characters")
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        raise ValueError(f"RenderedFile.path must not be absolute: {path!r}")
    if path.startswith(("/", "\\")):
        raise ValueError(f"RenderedFile.path must not be absolute: {path!r}")
    posix = PurePosixPath(path.replace("\\", "/"))
    if posix.is_absolute():
        raise ValueError(f"RenderedFile.path must not be absolute: {path!r}")
    parts = posix.parts
    if any(part == ".." for part in parts):
        raise ValueError(f"RenderedFile.path must not traverse parents: {path!r}")
    if not parts:
        raise ValueError(f"RenderedFile.path resolved to empty: {path!r}")
    return str(posix)


@dataclass(frozen=True, slots=True)
class RenderedFile:
    """单个已渲染文件的相对路径与内容。

    `path` 在构造时被校验：必须是非空、非绝对、不含 `..` 与 NUL 的 POSIX
    相对路径，且不带 Windows 盘符。这样下游写盘逻辑可以安全地把它拼到
    任何目标目录。
    """

    path: str
    content: str

    def __post_init__(self) -> None:
        normalized = _validate_relative_path(self.path)
        if normalized != self.path:
            object.__setattr__(self, "path", normalized)
        if not isinstance(self.content, str):
            raise TypeError(
                f"RenderedFile.content must be str, got {type(self.content).__name__}"
            )


__all__ = ["RenderedFile"]