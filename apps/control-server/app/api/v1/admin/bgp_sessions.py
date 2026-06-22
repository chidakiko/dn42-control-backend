from __future__ import annotations

"""BgpSession CRUD：节点上的一条 BGP 会话。

写端点接受完整 ``BgpSessionSpec`` payload，先经 ``dn42_schemas`` 校验，再由
``row.apply_spec`` 从校验过的 spec 单源投影出索引列 + ``spec`` 列（杜绝列/spec 漂移）。
每次写完调用 materializer 重出一个世代并广播。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dn42_schemas import BgpSessionSpec

from ....core.events import EventBus
from ....db.engine import Database
from ....db.models import BgpSession, Node, Peering
from ...deps import get_database, get_event_bus
from ._helpers import _errors_to_json, broadcast_change, materialize_change

router = APIRouter()


class SessionIn(BaseModel):
    """新增 BGP 会话：``spec`` 是 BgpSessionSpec 的 JSON dump（与 desired-state 一致）。"""

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    peering_id: int | None = None
    sort_order: int = 0


class SessionPatch(BaseModel):
    """局部更新：各字段为 ``None`` 表示不动；``clear_peering`` 显式解绑 peering。"""

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any] | None = None
    peering_id: int | None = None
    sort_order: int | None = None
    # PATCH 允许显式把 peering_id 置 None，需单独 flag 与"不传"区分。
    clear_peering: bool = False


class SessionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    node_id: str
    peering_id: int | None
    name: str
    remote_asn: int
    enabled: bool
    sort_order: int
    spec: dict[str, Any]


def _bgp_out(row: BgpSession) -> SessionOut:
    """``BgpSession`` 行 → API 响应。索引列直接读列（权威），完整定义读 ``spec``。"""

    return SessionOut(
        id=row.id,
        node_id=row.node_id,
        peering_id=row.peering_id,
        name=row.name,
        remote_asn=row.remote_asn,
        enabled=row.enabled,
        sort_order=row.sort_order,
        spec=dict(row.spec),
    )


def _validate_bgp_spec(spec: dict[str, Any]) -> BgpSessionSpec:
    """校验 BgpSessionSpec，失败抛 422（带字段级错误）。"""

    try:
        return BgpSessionSpec.model_validate(spec)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "invalid BgpSessionSpec", "errors": _errors_to_json(exc)},
        ) from exc


async def _get_session(session: AsyncSession, sid: int) -> BgpSession:
    """按 id 取会话，不存在抛 404。"""

    row = await session.get(BgpSession, sid)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown bgp session {sid}")
    return row


@router.get("/nodes/{node_id}/bgp-sessions", response_model=list[SessionOut])
async def list_sessions(node_id: str, db: Database = Depends(get_database)) -> list[SessionOut]:
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        rows = await session.execute(
            select(BgpSession)
            .where(BgpSession.node_id == node_id)
            .order_by(BgpSession.sort_order, BgpSession.id)
        )
        return [_bgp_out(row) for row in rows.scalars()]


@router.post(
    "/nodes/{node_id}/bgp-sessions",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    node_id: str,
    payload: SessionIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> SessionOut:
    spec = _validate_bgp_spec(payload.spec)
    reason = f"bgp session {spec.name} added"
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        if payload.peering_id is not None:
            peering = await session.get(Peering, payload.peering_id)
            if peering is None or peering.local_node_id != node_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"peering {payload.peering_id} not on node {node_id}",
                )
        row = BgpSession(
            node_id=node_id,
            peering_id=payload.peering_id,
            sort_order=payload.sort_order,
        )
        row.apply_spec(spec)
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as exc:  # UNIQUE(node_id, name)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"bgp session {spec.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        out = _bgp_out(row)
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return out


@router.get("/bgp-sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: int, db: Database = Depends(get_database)) -> SessionOut:
    async with db.session() as session:
        return _bgp_out(await _get_session(session, session_id))


@router.patch("/bgp-sessions/{session_id}", response_model=SessionOut)
async def update_session(
    session_id: int,
    payload: SessionPatch,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> SessionOut:
    new_spec: BgpSessionSpec | None = None
    if payload.spec is not None:
        new_spec = _validate_bgp_spec(payload.spec)

    async with db.session() as session:
        row = await _get_session(session, session_id)
        node_id = row.node_id

        if payload.peering_id is not None and not payload.clear_peering:
            peering = await session.get(Peering, payload.peering_id)
            if peering is None or peering.local_node_id != node_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"peering {payload.peering_id} not on node {node_id}",
                )
            row.peering_id = payload.peering_id
        if payload.clear_peering:
            row.peering_id = None

        if new_spec is not None:
            row.apply_spec(new_spec)
        if payload.sort_order is not None:
            row.sort_order = payload.sort_order

        try:
            await session.flush()
        except IntegrityError as exc:  # UNIQUE(node_id, name)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"bgp session {row.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        out = _bgp_out(row)
        reason = f"bgp session {out.name} updated"
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return out


@router.delete("/bgp-sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> None:
    async with db.session() as session:
        row = await _get_session(session, session_id)
        node_id = row.node_id
        reason = f"bgp session {row.name} deleted"
        await session.delete(row)
        await session.flush()
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)


__all__ = ["router"]
