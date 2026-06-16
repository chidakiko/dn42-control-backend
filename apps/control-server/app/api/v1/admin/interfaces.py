from __future__ import annotations

"""WgInterface CRUD：节点上的 wg / dummy / gre 接口。

写接口接受一个完整 ``InterfaceSpec`` payload，先由 ``dn42_schemas`` 校验，
再以 dump 后的 dict 落 ``spec`` 列。写完调用 materializer 重出一个世代并广播。
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from dn42_schemas import InterfaceKind, InterfaceSpec, WireGuardPortRangeSpec

from ....core.events import EventBus
from ....db.engine import Database
from ....db.models import Node, Peering, WgInterface
from ...deps import get_database, get_event_bus
from ._helpers import _errors_to_json, broadcast_change, materialize_change

router = APIRouter()


class InterfaceIn(BaseModel):
    """新增接口：``spec`` 是 InterfaceSpec 的 JSON dump（与 desired-state 中一致）。"""

    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    peering_id: int | None = None
    enabled: bool = True
    sort_order: int = 0


class InterfacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any] | None = None
    peering_id: int | None = Field(default=None)
    enabled: bool | None = None
    sort_order: int | None = None
    # PATCH 允许显式把 peering_id 置 None，需要单独的 flag 来区分。
    clear_peering: bool = False


class InterfaceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    node_id: str
    peering_id: int | None
    name: str
    kind: str
    enabled: bool
    sort_order: int
    spec: dict[str, Any]


def _iface_out(row: WgInterface) -> InterfaceOut:
    return InterfaceOut(
        id=row.id,
        node_id=row.node_id,
        peering_id=row.peering_id,
        name=row.name,
        kind=row.kind,
        enabled=row.enabled,
        sort_order=row.sort_order,
        spec=dict(row.spec),
    )


def _validate_iface_spec(spec: dict[str, Any]) -> InterfaceSpec:
    try:
        return InterfaceSpec.model_validate(spec)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "invalid InterfaceSpec", "errors": _errors_to_json(exc)},
        ) from exc


async def _validate_wireguard_port_policy(
    session: Any,
    *,
    node_id: str,
    iface_spec: InterfaceSpec,
    enabled: bool,
    current_iface_id: int | None = None,
) -> None:
    if not enabled or iface_spec.kind != InterfaceKind.WIREGUARD or iface_spec.listen_port is None:
        return

    node = await session.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")

    runtime = dict(node.base_template.get("runtime") or {})
    range_payload = runtime.get("wireguard_port_range")
    if range_payload is not None:
        try:
            port_range = WireGuardPortRangeSpec.model_validate(range_payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": "invalid node runtime.wireguard_port_range",
                    "errors": _errors_to_json(exc),
                },
            ) from exc
        if not port_range.contains(iface_spec.listen_port):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "wireguard listen_port must be inside node "
                    f"runtime.wireguard_port_range {port_range.start}-{port_range.end}"
                ),
            )

    rows = await session.execute(
        select(WgInterface).where(WgInterface.node_id == node_id, WgInterface.enabled.is_(True))
    )
    for row in rows.scalars():
        if current_iface_id is not None and row.id == current_iface_id:
            continue
        try:
            existing = InterfaceSpec.model_validate(row.spec)
        except ValidationError:
            continue
        if (
            existing.kind == InterfaceKind.WIREGUARD
            and existing.listen_port == iface_spec.listen_port
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"wireguard listen_port {iface_spec.listen_port} is already used by "
                    f"interface {row.name!r} on node {node_id}"
                ),
            )


@router.get("/nodes/{node_id}/interfaces", response_model=list[InterfaceOut])
async def list_interfaces(node_id: str, db: Database = Depends(get_database)) -> list[InterfaceOut]:
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        rows = await session.execute(
            select(WgInterface)
            .where(WgInterface.node_id == node_id)
            .order_by(WgInterface.sort_order, WgInterface.id)
        )
        return [_iface_out(row) for row in rows.scalars()]


@router.post(
    "/nodes/{node_id}/interfaces",
    response_model=InterfaceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_interface(
    node_id: str,
    payload: InterfaceIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> InterfaceOut:
    iface_spec = _validate_iface_spec(payload.spec)
    reason = f"interface {iface_spec.name} added"

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
        await _validate_wireguard_port_policy(
            session,
            node_id=node_id,
            iface_spec=iface_spec,
            enabled=payload.enabled,
        )
        row = WgInterface(
            node_id=node_id,
            peering_id=payload.peering_id,
            name=iface_spec.name,
            kind=iface_spec.kind.value,
            enabled=payload.enabled,
            spec=iface_spec.model_dump(mode="json"),
            sort_order=payload.sort_order,
        )
        session.add(row)
        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"interface {iface_spec.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        result = _iface_out(row)
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return result


async def _get_iface(session: Any, iface_id: int) -> WgInterface:
    row = await session.get(WgInterface, iface_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown interface {iface_id}")
    return row


@router.get("/interfaces/{iface_id}", response_model=InterfaceOut)
async def get_interface(iface_id: int, db: Database = Depends(get_database)) -> InterfaceOut:
    async with db.session() as session:
        return _iface_out(await _get_iface(session, iface_id))


@router.patch("/interfaces/{iface_id}", response_model=InterfaceOut)
async def update_interface(
    iface_id: int,
    payload: InterfacePatch,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> InterfaceOut:
    new_spec_validated: InterfaceSpec | None = None
    if payload.spec is not None:
        new_spec_validated = _validate_iface_spec(payload.spec)

    async with db.session() as session:
        row = await _get_iface(session, iface_id)
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

        effective_spec = new_spec_validated or InterfaceSpec.model_validate(row.spec)
        effective_enabled = row.enabled if payload.enabled is None else payload.enabled
        await _validate_wireguard_port_policy(
            session,
            node_id=node_id,
            iface_spec=effective_spec,
            enabled=effective_enabled,
            current_iface_id=iface_id,
        )

        if new_spec_validated is not None:
            row.spec = new_spec_validated.model_dump(mode="json")
            row.name = new_spec_validated.name
            row.kind = new_spec_validated.kind.value
        if payload.enabled is not None:
            row.enabled = payload.enabled
        if payload.sort_order is not None:
            row.sort_order = payload.sort_order

        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"interface {row.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        result = _iface_out(row)
        reason = f"interface {result.name} updated"
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return result


@router.delete("/interfaces/{iface_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_interface(
    iface_id: int,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> None:
    async with db.session() as session:
        row = await _get_iface(session, iface_id)
        node_id = row.node_id
        reason = f"interface {row.name} deleted"
        await session.delete(row)
        await session.flush()
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)


__all__ = ["router"]
