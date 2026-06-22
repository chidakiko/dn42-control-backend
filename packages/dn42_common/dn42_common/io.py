from __future__ import annotations

"""跨包共享的原子文件写盘工具。

agent 的身份 / 缓存 desired-state / 容器定义记录都需要"要么写完整、要么不写"
的落盘语义,避免崩溃在写一半时留下损坏文件。统一在这里实现 tmp + 同名替换,
避免各处重复。
"""

import json
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str) -> None:
    """原子写入文本:先写同目录临时文件,再 ``replace`` 覆盖目标。

    ``replace`` 在同文件系统上是原子的,读者要么看到旧内容、要么看到新内容,
    绝不会看到写一半的中间态。缺失的父目录会一并创建。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as file:
        file.write(text)
    tmp.replace(path)


def atomic_write_json(path: Path, payload: Any, *, indent: int = 2, sort_keys: bool = True) -> None:
    """把 ``payload`` 序列化为 JSON 并原子写入(末尾带换行)。"""

    text = json.dumps(payload, indent=indent, sort_keys=sort_keys, ensure_ascii=False)
    atomic_write_text(path, text + "\n")


__all__ = ["atomic_write_json", "atomic_write_text"]
