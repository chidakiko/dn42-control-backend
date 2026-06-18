from __future__ import annotations

"""BgpSession CRUD：节点上的一条 BGP 会话。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select

from dn42_schemas import BgpSessionSpec

from ....core.events import EventBus
from ....db.engine import Database
from ....db.models import BgpSession, Node, Peering
from ...deps import get_database, get_event_bus
from ._helpers import _errors_to_json, broadcast_change, materialize_change

router = APIRouter()


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    peering_id: int | None = None
    sort_order: int = 0


class SessionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any] | None = None
    peering_id: int | None = None
    sort_order: int | None = None
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


def _out(row: BgpSession) -> SessionOut:
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


def _validate(spec: dict[str, Any]) -> BgpSessionSpec:
    try:
        return BgpSessionSpec.model_validate(spec)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "invalid BgpSessionSpec", "errors": _errors_to_json(exc)},
        ) from exc


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
        return [_out(row) for row in rows.scalars()]


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
    spec = _validate(payload.spec)
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
            name=spec.name,
            remote_asn=spec.remote_asn,
            enabled=spec.enabled,
            spec=spec.model_dump(mode="json"),
            sort_order=payload.sort_order,
        )
        session.add(row)
        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"bgp session {spec.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        out = _out(row)
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return out


async def _get(session: Any, sid: int) -> BgpSession:
    row = await session.get(BgpSession, sid)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown bgp session {sid}")
    return row


@router.get("/bgp-sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: int, db: Database = Depends(get_database)) -> SessionOut:
    async with db.session() as session:
        return _out(await _get(session, session_id))


@router.patch("/bgp-sessions/{session_id}", response_model=SessionOut)
async def update_session(
    session_id: int,
    payload: SessionPatch,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> SessionOut:
    new_spec: BgpSessionSpec | None = None
    if payload.spec is not None:
        new_spec = _validate(payload.spec)

    async with db.session() as session:
        row = await _get(session, session_id)
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
            row.name = new_spec.name
            row.remote_asn = new_spec.remote_asn
            row.enabled = new_spec.enabled
            row.spec = new_spec.model_dump(mode="json")
        if payload.sort_order is not None:
            row.sort_order = payload.sort_order

        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"bgp session {row.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        out = _out(row)
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
        row = await _get(session, session_id)
        node_id = row.node_id
        reason = f"bgp session {row.name} deleted"
        await session.delete(row)
        await session.flush()
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)


__all__ = ["router"]
