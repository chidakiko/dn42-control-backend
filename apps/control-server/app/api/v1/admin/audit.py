from __future__ import annotations

"""Admin 操作审计日志查询。

- ``GET /admin/audit-log``：按时间倒序返回最近的 Admin 写操作记录
  （由 ``main.audit_admin_writes`` 中间件落库，含鉴权失败的尝试）。
"""

from fastapi import APIRouter, Depends, Query

from ....services.audit import AuditLogStore
from ...deps import get_audit

router = APIRouter()


@router.get("/audit-log")
async def list_audit_log(
    limit: int = Query(default=100, ge=1, le=1000),
    audit: AuditLogStore = Depends(get_audit),
) -> dict:
    entries = await audit.list_recent(limit=limit)
    return {"entries": entries}


__all__ = ["router"]
