from __future__ import annotations

"""待审批注册请求仓库 ``PendingRegistrationStore``。

未知节点带合法 enrollment_token 注册时，``record`` 落一条 pending；管理员通过
``list_pending`` 查看，``set_status`` 审批。审批通过本身不签发 token——真正的
provision + token 签发仍走 admin provision / agent-token API，这里只负责"门禁名单"。
"""

from datetime import datetime, timezone

from sqlalchemy import select

from ..db.engine import Database
from ..db.models import PendingRegistration


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PendingRegistrationStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def record(
        self, requested_node_id: str, *, hostname: str | None, inventory: dict
    ) -> None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(PendingRegistration).where(
                    PendingRegistration.requested_node_id == requested_node_id,
                    PendingRegistration.status == "pending",
                )
            )
            if row is None:
                session.add(
                    PendingRegistration(
                        requested_node_id=requested_node_id,
                        hostname=hostname,
                        inventory=inventory,
                    )
                )
            else:
                row.hostname = hostname
                row.inventory = inventory
                row.updated_at = _utc_now()

    async def status_for(self, requested_node_id: str) -> str | None:
        """返回该节点最近一条请求的状态；从未注册过则 None。"""

        async with self._db.session() as session:
            row = await session.scalar(
                select(PendingRegistration)
                .where(PendingRegistration.requested_node_id == requested_node_id)
                .order_by(PendingRegistration.id.desc())
                .limit(1)
            )
            return row.status if row is not None else None

    async def list_all(self, *, status: str | None = None) -> list[dict]:
        async with self._db.session() as session:
            stmt = select(PendingRegistration)
            if status is not None:
                stmt = stmt.where(PendingRegistration.status == status)
            stmt = stmt.order_by(PendingRegistration.id.desc())
            rows = await session.execute(stmt)
            return [self._to_dict(row) for row in rows.scalars()]

    async def set_status(
        self, registration_id: int, status: str, *, note: str | None = None
    ) -> dict | None:
        async with self._db.session() as session:
            row = await session.get(PendingRegistration, registration_id)
            if row is None:
                return None
            row.status = status
            if note is not None:
                row.note = note
            row.updated_at = _utc_now()
            return self._to_dict(row)

    @staticmethod
    def _to_dict(row: PendingRegistration) -> dict:
        return {
            "id": row.id,
            "requested_node_id": row.requested_node_id,
            "hostname": row.hostname,
            "inventory": row.inventory,
            "status": row.status,
            "note": row.note,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


__all__ = ["PendingRegistrationStore"]
