from __future__ import annotations

"""ISO-8601 时间戳校验。"""

from datetime import datetime


def validate_iso8601_timestamp(value: str) -> str:
    """校验 ISO-8601 时间戳，返回原值。

    使用 `datetime.fromisoformat`，接受 `2026-06-03T12:00:00+00:00` /
    `2026-06-03T12:00:00Z` 等常见形态（Python 3.11+ 已支持 `Z` 后缀）。
    """

    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty string")
    candidate = value
    # Python 3.11 之前 fromisoformat 不接受 Z；这里做一次兼容兜底。
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    return value


__all__ = ["validate_iso8601_timestamp"]
