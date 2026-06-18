from __future__ import annotations

"""EnrollmentToken 的 DB 仓库（哈希存储 / 过期 / 一次性 / 按节点绑定）。

与 ``TokenStore`` 同安全模型：明文 secret 仅在创建瞬间返回一次，DB 只存
sha256；``resolve`` 对来访 token 做哈希查找。语义上是注册门票：

- ``node_id`` 非空 → 只允许该节点用它注册；为空 → 任意节点可用（通用门票）。
- ``expires_at`` 非空且已过期 → 拒绝。
- ``used_at`` 非空 → 已消费过，拒绝（一次性）。注册结果为 pending-approval
  时不消费——agent 审批通过后还要拿同一张门票回来换 token。
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from ..db.engine import Database
from ..db.models import EnrollmentToken
from .tokens import hash_token


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return _utc_now() >= expires_at


def enrollment_token_id(secret: str) -> str:
    """固定字面量 enrollment token 的确定性主键 id（非机密）。"""

    return f"ent_{hash_token(secret)[:12]}"


@dataclass(frozen=True)
class IssuedEnrollmentToken:
    """一次创建结果。``secret`` 是完整注册 token，仅此一次可见。"""

    token_id: str
    secret: str
    node_id: str | None


@dataclass(frozen=True)
class EnrollmentGrant:
    """``resolve`` 成功后的注册许可。"""

    token_id: str
    node_id: str | None


class EnrollmentTokenStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def create(
        self,
        *,
        token: str | None = None,
        node_id: str | None = None,
        description: str | None = None,
        expires_at: datetime | None = None,
    ) -> IssuedEnrollmentToken:
        """创建（或登记固定字面量）enrollment token，返回明文 secret。"""

        if token is not None:
            secret = token
            token_id = enrollment_token_id(token)
        else:
            token_id = f"ent_{secrets.token_hex(6)}"
            secret = f"{token_id}.{secrets.token_urlsafe(24)}"

        async with self._db.session() as session:
            session.add(
                EnrollmentToken(
                    token=token_id,
                    token_hash=hash_token(secret),
                    node_id=node_id,
                    description=description,
                    expires_at=expires_at,
                )
            )
        return IssuedEnrollmentToken(token_id=token_id, secret=secret, node_id=node_id)

    async def resolve(self, secret: str) -> EnrollmentGrant | None:
        """按哈希查找有效（未过期、未消费）的注册门票。"""

        async with self._db.session() as session:
            row = await session.scalar(
                select(EnrollmentToken).where(
                    EnrollmentToken.token_hash == hash_token(secret)
                )
            )
            if row is None or row.used_at is not None or _is_expired(row.expires_at):
                return None
            return EnrollmentGrant(token_id=row.token, node_id=row.node_id)

    async def mark_used(self, token_id: str) -> None:
        """消费门票（注册成功签发 agent token 后调用）。"""

        async with self._db.session() as session:
            row = await session.get(EnrollmentToken, token_id)
            if row is not None and row.used_at is None:
                row.used_at = _utc_now()

    async def list_all(self) -> list[EnrollmentToken]:
        async with self._db.session() as session:
            rows = await session.execute(
                select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc())
            )
            return list(rows.scalars())

    async def delete(self, token_id: str) -> None:
        async with self._db.session() as session:
            row = await session.get(EnrollmentToken, token_id)
            if row is not None:
                await session.delete(row)


__all__ = [
    "EnrollmentGrant",
    "EnrollmentTokenStore",
    "IssuedEnrollmentToken",
    "enrollment_token_id",
]
