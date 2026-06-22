from __future__ import annotations

"""规范化 JSON 序列化 + 内容寻址哈希。

控制面与 agent 在不同进程对同一对象算出的字节序列必须逐字节一致,才能用于
世代哈希比对与容器内容寻址身份。固定排序键、紧凑分隔符、``ensure_ascii=False``
+ UTF-8 编码,集中一处定义,避免各处各写一遍序列化参数导致悄悄漂移。
"""

import hashlib
import json
from typing import Any


def canonical_json_dumps(obj: Any) -> str:
    """返回 ``obj`` 的规范化 JSON 字符串(排序键、紧凑分隔符、非 ASCII 原样)。"""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_sha256_hex(obj: Any) -> str:
    """``canonical_json_dumps(obj)`` 的 SHA-256 十六进制摘要。"""

    return hashlib.sha256(canonical_json_dumps(obj).encode("utf-8")).hexdigest()


__all__ = ["canonical_json_dumps", "canonical_sha256_hex"]
