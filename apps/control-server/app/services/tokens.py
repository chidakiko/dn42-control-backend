from __future__ import annotations

"""Agent Bearer token 的 DB 仓库（哈希存储 / 过期 / 轮换）。

安全模型：

- **明文永不落库**。随机签发的 token 形如 ``<id>.<secret>``，其中 ``id`` 是
  非机密的查找键（作为 ``AgentToken.token`` 主键），``secret`` 仅在签发瞬间返回一次。
  指定字面量 token（bootstrap / provision 固定 token）同样只存哈希，主键是从
  哈希派生的 ``agt_*`` id（确定性，重复 provision 幂等）。
- **解析只走哈希**：``resolve`` 对来访 Bearer 做 sha256 后按 ``token_hash`` 查找。
- **过期**：``expires_at`` 非空且已过期的 token 一律拒绝。
- **轮换**：``rotate`` = 撤销旧 token + 为同节点签发新 token，旧的立即失效。

token 是高熵随机串，用 sha256 单向摘要即可（无需慢哈希）。
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from ..db.engine import Database
from ..db.models import AgentToken


def hash_token(secret: str) -> str:
    """完整 Bearer secret 的 sha256 摘要（DB 中唯一的校验依据）。"""

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def literal_token_id(secret: str) -> str:
    """固定字面量 token 的确定性主键 id（非机密，可安全展示）。"""

    return f"agt_{hash_token(secret)[:12]}"




def _is_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= expires_at


@dataclass(frozen=True)
class TokenPrincipal:
    """通过 Bearer token 解析出的调用主体。

    Attributes:
        token: 调用时携带的原始 Bearer token（含 secret）。
        node_id: token 绑定的节点身份；agent 只能操作自己的节点。
    """

    token: str
    node_id: str


@dataclass(frozen=True)
class IssuedToken:
    """一次签发结果。``secret`` 是完整 Bearer，仅此一次可见。"""

    token_id: str
    secret: str
    node_id: str
    agent_id: str


class TokenStore:
    """``token -> node_id`` 的 DB 持久化映射，支持哈希 / 过期 / 轮换。"""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def issue(
        self,
        node_id: str,
        *,
        token: str | None = None,
        agent_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> str:
        """签发或登记一条 token，返回完整 Bearer secret（仅此一次可见）。

        - 传入 ``token``：登记固定字面量（bootstrap / provision），只存哈希，
          主键为从哈希派生的确定性 id。
        - 不传：生成 ``<id>.<secret>``，主键只存 ``id``，DB 仅留哈希。
        """

        result = await self.issue_detailed(
            node_id, token=token, agent_id=agent_id, expires_at=expires_at
        )
        return result.secret

    async def issue_detailed(
        self,
        node_id: str,
        *,
        token: str | None = None,
        agent_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> IssuedToken:
        if token is not None:
            # 固定字面量：id 从哈希确定性派生，重复登记同一字面量命中同一行。
            secret = token
            token_id = literal_token_id(token)
        else:
            token_id = f"agt_{secrets.token_hex(6)}"
            secret = f"{token_id}.{secrets.token_urlsafe(24)}"

        token_hash = hash_token(secret)
        resolved_agent_id = agent_id or f"{node_id}-agent"
        async with self._db.session() as session:
            existing = await session.get(AgentToken, token_id)
            if existing is None:
                session.add(
                    AgentToken(
                        token=token_id,
                        token_hash=token_hash,
                        node_id=node_id,
                        agent_id=resolved_agent_id,
                        expires_at=expires_at,
                    )
                )
            else:
                existing.node_id = node_id
                existing.token_hash = token_hash
                existing.agent_id = resolved_agent_id
                existing.expires_at = expires_at
                existing.revoked_at = None
        return IssuedToken(
            token_id=token_id,
            secret=secret,
            node_id=node_id,
            agent_id=resolved_agent_id,
        )

    async def resolve(self, token: str) -> TokenPrincipal | None:
        token_hash = hash_token(token)
        async with self._db.session() as session:
            row = await session.scalar(
                select(AgentToken).where(AgentToken.token_hash == token_hash)
            )
            if row is None or row.revoked_at is not None:
                return None
            if _is_expired(row.expires_at):
                return None
            return TokenPrincipal(token=token, node_id=row.node_id)

    async def revoke(self, token: str) -> None:
        """撤销并删除 token。``token`` 可传主键 id、完整 ``<id>.<secret>`` 或字面量。"""

        token_id = token.split(".", 1)[0] if "." in token else token
        async with self._db.session() as session:
            row = await session.get(AgentToken, token_id)
            if row is None:
                # 不是 id：按哈希找（完整 secret / 固定字面量）。
                row = await session.scalar(
                    select(AgentToken).where(AgentToken.token_hash == hash_token(token))
                )
            if row is None:
                return
            await session.delete(row)

    async def rotate(
        self,
        token_id: str,
        *,
        agent_id: str | None = None,
        expires_at: datetime | None = None,
    ) -> IssuedToken | None:
        """轮换：撤销旧 token，给同节点签发新 token。旧的立即失效。"""

        async with self._db.session() as session:
            row = await session.get(AgentToken, token_id)
            if row is None:
                return None
            node_id = row.node_id
            keep_agent_id = agent_id or row.agent_id
            await session.delete(row)

        return await self.issue_detailed(
            node_id, agent_id=keep_agent_id, expires_at=expires_at
        )


__all__ = [
    "IssuedToken",
    "TokenPrincipal",
    "TokenStore",
    "hash_token",
    "literal_token_id",
]
