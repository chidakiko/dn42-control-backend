from __future__ import annotations

"""Admin 操作审计日志 ORM 模型：``admin_audit_log``。"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminAuditLog(Base):
    """append-only 的 Admin 写操作记录。

    每条记录对应一次到达 ``/api/v1/admin`` 的变更类 HTTP 请求（POST / PUT /
    PATCH / DELETE），无论鉴权是否通过——失败的尝试同样是审计要素。
    ``actor`` 为鉴权通过后的主体标识（当前单 token 模型下为 ``admin``），
    鉴权失败时为空。
    """

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str | None] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, server_default=func.now(), index=True
    )


__all__ = ["AdminAuditLog"]
