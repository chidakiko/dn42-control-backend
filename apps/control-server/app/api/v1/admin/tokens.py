from __future__ import annotations

"""Agent Bearer token 管理。

- ``GET    /admin/nodes/{node_id}/agent-tokens``：列出该节点所有 token 的**元信息**
  （token id / 过期 / 撤销态）；不含 secret，secret 只在签发瞬间返回一次。
- ``POST   /admin/nodes/{node_id}/agent-tokens``：签发一条新 token，响应里带一次性
  ``secret``。可选传入自定义 ``token`` 字面量以兼容 bootstrap，或 ``ttl_seconds`` 设过期。
- ``POST   /admin/agent-tokens/{token_id}/rotate``：轮换——撤销旧的并签发新 token。
- ``DELETE /admin/agent-tokens/{token_id}``：撤销。
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from dn42_common import validate_agent_token

from ....db.engine import Database
from ....db.models import AgentToken, Node
from ....services.tokens import TokenStore
from ...deps import get_database, get_tokens

router = APIRouter()


class AgentTokenIssueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = None
    agent_id: str | None = None
    ttl_seconds: int | None = Field(default=None, ge=1)


class AgentTokenRotateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ttl_seconds: int | None = Field(default=None, ge=1)


class AgentTokenOut(BaseModel):
    """token 元信息。``token`` 是非机密 id；``secret`` 仅在签发 / 轮换响应里出现一次。"""

    model_config = ConfigDict(extra="forbid")

    token: str
    secret: str | None = None
    node_id: str
    agent_id: str
    issued_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


def _out(row: AgentToken, *, secret: str | None = None) -> AgentTokenOut:
    return AgentTokenOut(
        token=row.token,
        secret=secret,
        node_id=row.node_id,
        agent_id=row.agent_id,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


def _ttl_to_expiry(ttl_seconds: int | None) -> datetime | None:
    if ttl_seconds is None:
        return None
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


@router.get("/nodes/{node_id}/agent-tokens", response_model=list[AgentTokenOut])
async def list_node_tokens(node_id: str, db: Database = Depends(get_database)) -> list[AgentTokenOut]:
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        rows = await session.execute(
            select(AgentToken).where(AgentToken.node_id == node_id).order_by(AgentToken.issued_at.desc())
        )
        return [_out(row) for row in rows.scalars()]


@router.post(
    "/nodes/{node_id}/agent-tokens",
    response_model=AgentTokenOut,
    status_code=status.HTTP_201_CREATED,
)
async def issue_node_token(
    node_id: str,
    payload: AgentTokenIssueIn | None = None,
    db: Database = Depends(get_database),
    tokens: TokenStore = Depends(get_tokens),
) -> AgentTokenOut:
    payload = payload or AgentTokenIssueIn()
    if payload.token is not None:
        # 字面量 token 由运维指定（bootstrap 兼容）：强制 base64url + 最小熵长度。
        # 自动生成的 token 形如 <id>.<secret>，不经此路径。
        try:
            validate_agent_token(payload.token)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid agent token shape: {exc}",
            ) from exc
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")

    issued = await tokens.issue_detailed(
        node_id,
        token=payload.token,
        agent_id=payload.agent_id,
        expires_at=_ttl_to_expiry(payload.ttl_seconds),
    )

    async with db.session() as session:
        row = await session.get(AgentToken, issued.token_id)
        assert row is not None
        # 签发响应的 token 字段即完整 secret——这是调用方唯一一次拿到它的机会。
        out = _out(row, secret=issued.secret)
        out.token = issued.secret
        return out


@router.post(
    "/agent-tokens/{token_id}/rotate",
    response_model=AgentTokenOut,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_token(
    token_id: str,
    payload: AgentTokenRotateIn | None = None,
    db: Database = Depends(get_database),
    tokens: TokenStore = Depends(get_tokens),
) -> AgentTokenOut:
    payload = payload or AgentTokenRotateIn()
    issued = await tokens.rotate(token_id, expires_at=_ttl_to_expiry(payload.ttl_seconds))
    if issued is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown token {token_id}"
        )
    async with db.session() as session:
        row = await session.get(AgentToken, issued.token_id)
        assert row is not None
        out = _out(row, secret=issued.secret)
        out.token = issued.secret
        return out


@router.delete("/agent-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: str, tokens: TokenStore = Depends(get_tokens)) -> None:
    await tokens.revoke(token_id)


__all__ = ["router"]
