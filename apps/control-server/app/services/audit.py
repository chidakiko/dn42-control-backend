from __future__ import annotations

"""Admin 操作审计仓库 ``AuditLogStore``。

写入方与 ``node_status`` 的 append-only 模式一致，但审计日志不做自动裁剪——
保留完整历史是审计的意义所在，清理策略交给运维（DB 层归档 / 定期导出）。
"""

from sqlalchemy import select

from ..db.engine import Database
from ..db.models import AdminAuditLog


class AuditLogStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def record(
        self,
        *,
        actor: str | None,
        method: str,
        path: str,
        status_code: int,
        detail: dict | None = None,
    ) -> None:
        async with self._db.session() as session:
            session.add(
                AdminAuditLog(
                    actor=actor,
                    method=method,
                    path=path,
                    status_code=status_code,
                    detail=detail or {},
                )
            )

    async def list_recent(self, *, limit: int = 100) -> list[dict]:
        async with self._db.session() as session:
            rows = await session.execute(
                select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(limit)
            )
            return [self._to_dict(row) for row in rows.scalars()]

    @staticmethod
    def _to_dict(row: AdminAuditLog) -> dict:
        return {
            "id": row.id,
            "actor": row.actor,
            "method": row.method,
            "path": row.path,
            "status_code": row.status_code,
            "detail": row.detail,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


__all__ = ["AuditLogStore"]
