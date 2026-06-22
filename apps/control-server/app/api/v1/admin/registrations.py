from __future__ import annotations

"""待审批 agent 注册管理（管理面）。

- ``GET  /admin/registrations``：列出注册请求（可按 status 过滤）。
- ``POST /admin/registrations/{id}/approve``：标记为 approved（放行名单）。
- ``POST /admin/registrations/{id}/reject``：标记为 rejected。

注意：approve 只是把节点放进"门禁名单"，并不自动 provision / 发 token。
真正下发仍需调用 ``POST /admin/provision``（或逐资源 CRUD）来落 DesiredState，
之后该节点的 agent 才能在 ``/agent/register`` 拿到 token。
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from ....services.pending_registrations import PendingRegistrationStore
from ...deps import get_pending_registrations

router = APIRouter()


class RegistrationDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None


@router.get("/registrations")
async def list_registrations(
    status_filter: Literal["pending", "approved", "rejected"] | None = Query(
        default=None, alias="status"
    ),
    pending: PendingRegistrationStore = Depends(get_pending_registrations),
) -> dict:
    rows = await pending.list_all(status=status_filter)
    return {"registrations": rows}


@router.post("/registrations/{registration_id}/approve")
async def approve_registration(
    registration_id: int,
    payload: RegistrationDecisionIn | None = None,
    pending: PendingRegistrationStore = Depends(get_pending_registrations),
) -> dict:
    payload = payload or RegistrationDecisionIn()
    row = await pending.set_status(registration_id, "approved", note=payload.note)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown registration {registration_id}",
        )
    return row


@router.post("/registrations/{registration_id}/reject")
async def reject_registration(
    registration_id: int,
    payload: RegistrationDecisionIn | None = None,
    pending: PendingRegistrationStore = Depends(get_pending_registrations),
) -> dict:
    payload = payload or RegistrationDecisionIn()
    row = await pending.set_status(registration_id, "rejected", note=payload.note)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown registration {registration_id}",
        )
    return row


__all__ = ["router"]
