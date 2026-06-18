from __future__ import annotations

"""DnsZone CRUD：节点本地 DNS 区域。

注意：``Node.base_template.dns`` 必须存在（且包含 ``bind_addresses`` 等顶层字段），
否则即使写入 DnsZone 行，materialize 也不会把 ``dns`` 段加进 DesiredState；
那是一种"节点不部署本地 DNS"的合法情形。新增区域时若 ``base_template.dns``
为空,会在响应里告知调用方。
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select

from dn42_schemas import DnsZoneSpec

from ....core.events import EventBus
from ....db.engine import Database
from ....db.models import DnsZone, Node
from ...deps import get_database, get_event_bus
from ._helpers import _errors_to_json, broadcast_change, materialize_change

router = APIRouter()


class DnsZoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    enabled: bool = True


class DnsZonePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any] | None = None
    enabled: bool | None = None


class DnsZoneOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    node_id: str
    name: str
    enabled: bool
    spec: dict[str, Any]
    created_at: datetime
    updated_at: datetime


def _out(row: DnsZone) -> DnsZoneOut:
    return DnsZoneOut(
        id=row.id,
        node_id=row.node_id,
        name=row.name,
        enabled=row.enabled,
        spec=dict(row.spec),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _validate(spec: dict[str, Any]) -> DnsZoneSpec:
    try:
        return DnsZoneSpec.model_validate(spec)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "invalid DnsZoneSpec", "errors": _errors_to_json(exc)},
        ) from exc


@router.get("/nodes/{node_id}/dns-zones", response_model=list[DnsZoneOut])
async def list_zones(node_id: str, db: Database = Depends(get_database)) -> list[DnsZoneOut]:
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        rows = await session.execute(
            select(DnsZone).where(DnsZone.node_id == node_id).order_by(DnsZone.name)
        )
        return [_out(row) for row in rows.scalars()]


@router.post(
    "/nodes/{node_id}/dns-zones",
    response_model=DnsZoneOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_zone(
    node_id: str,
    payload: DnsZoneIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> DnsZoneOut:
    zone_spec = _validate(payload.spec)
    reason = f"dns zone {zone_spec.zone} added"
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        row = DnsZone(
            node_id=node_id,
            name=zone_spec.zone,
            enabled=payload.enabled,
            spec=zone_spec.model_dump(mode="json"),
        )
        session.add(row)
        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"dns zone {zone_spec.zone!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        out = _out(row)
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return out


async def _get(session: Any, zid: int) -> DnsZone:
    row = await session.get(DnsZone, zid)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown dns zone {zid}")
    return row


@router.get("/dns-zones/{zone_id}", response_model=DnsZoneOut)
async def get_zone(zone_id: int, db: Database = Depends(get_database)) -> DnsZoneOut:
    async with db.session() as session:
        return _out(await _get(session, zone_id))


@router.patch("/dns-zones/{zone_id}", response_model=DnsZoneOut)
async def update_zone(
    zone_id: int,
    payload: DnsZonePatch,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> DnsZoneOut:
    new_spec: DnsZoneSpec | None = None
    if payload.spec is not None:
        new_spec = _validate(payload.spec)

    async with db.session() as session:
        row = await _get(session, zone_id)
        node_id = row.node_id
        if new_spec is not None:
            row.name = new_spec.zone
            row.spec = new_spec.model_dump(mode="json")
        if payload.enabled is not None:
            row.enabled = payload.enabled
        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"dns zone {row.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        out = _out(row)
        reason = f"dns zone {out.name} updated"
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return out


@router.delete("/dns-zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(
    zone_id: int,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> None:
    async with db.session() as session:
        row = await _get(session, zone_id)
        node_id = row.node_id
        reason = f"dns zone {row.name} deleted"
        await session.delete(row)
        await session.flush()
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)


__all__ = ["router"]
