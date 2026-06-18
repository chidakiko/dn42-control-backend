from __future__ import annotations

"""统一的 UTC 时间工具。"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """返回当前 UTC `datetime`，已附带时区信息。"""

    return datetime.now(timezone.utc).replace(microsecond=0)


def utc_now_iso() -> str:
    """返回 ISO 8601 字符串形式的当前 UTC 时间。"""

    return utc_now().isoformat()


__all__ = ["utc_now", "utc_now_iso"]
