from __future__ import annotations

"""EnrollmentToken 管理。

EnrollmentToken 是 agent 首次 ``/agent/register`` 时携带的一次性注册门票。
与 agent token 同安全模型：DB 只存哈希，明文 secret 仅在创建响应中出现一次；
列表 / 删除都以非机密的 ``token_id``（``ent_*``）为键。

``node_id`` 非空表示门票绑定到该节点，register 时强制校验；为空为通用门票。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from dn42_common import validate_agent_token

from ....db.engine import Database
from ....db.models import EnrollmentToken, Node
from ....services.enrollment import EnrollmentTokenStore, enrollment_token_id
from ...deps import get_database, get_enrollment_tokens

router = APIRouter()


class EnrollmentTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str | None = Field(default=None, min_length=1, max_length=128)
    node_id: str | None = None
    description: str | None = None
    expires_at: datetime | None = None


class EnrollmentTokenOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_id: str
    node_id: str | None
    description: str | None
    expires_at: datetime | None
    used_at: datetime | None
    created_at: datetime


class EnrollmentTokenCreated(EnrollmentTokenOut):
    """创建响应额外携带明文 secret，仅此一次可见。"""

    secret: str


def _out(row: EnrollmentToken) -> EnrollmentTokenOut:
    return EnrollmentTokenOut(
        token_id=row.token,
        node_id=row.node_id,
        description=row.description,
        expires_at=row.expires_at,
        used_at=row.used_at,
        created_at=row.created_at,
    )


@router.get("/enrollment-tokens", response_model=list[EnrollmentTokenOut])
async def list_enrollment_tokens(
    store: EnrollmentTokenStore = Depends(get_enrollment_tokens),
) -> list[EnrollmentTokenOut]:
    return [_out(row) for row in await store.list_all()]


@router.post(
    "/enrollment-tokens",
    response_model=EnrollmentTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_enrollment_token(
    payload: EnrollmentTokenIn | None = None,
    db: Database = Depends(get_database),
    store: EnrollmentTokenStore = Depends(get_enrollment_tokens),
) -> EnrollmentTokenCreated:
    payload = payload or EnrollmentTokenIn()
    if payload.token is not None:
        # 运维显式指定字面量门票时强制 base64url + 最小熵长度，挡掉弱口令 /
        # 被截断的 token。自动生成的门票本就高熵，不走这条校验。
        try:
            validate_agent_token(payload.token)
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid enrollment token shape: {exc}",
            ) from exc
    async with db.session() as session:
        if payload.token is not None:
            existing = await session.get(
                EnrollmentToken, enrollment_token_id(payload.token)
            )
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="enrollment token already exists",
                )
        if payload.node_id is not None and await session.get(Node, payload.node_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown node {payload.node_id}",
            )
    issued = await store.create(
        token=payload.token,
        node_id=payload.node_id,
        description=payload.description,
        expires_at=payload.expires_at,
    )
    async with db.session() as session:
        row = await session.get(EnrollmentToken, issued.token_id)
        assert row is not None
        return EnrollmentTokenCreated(secret=issued.secret, **_out(row).model_dump())


@router.delete("/enrollment-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_enrollment_token(
    token_id: str,
    store: EnrollmentTokenStore = Depends(get_enrollment_tokens),
) -> None:
    await store.delete(token_id)


__all__ = ["router"]
