from __future__ import annotations

"""Peering CRUD：节点对等关系的元信息（不直接进入 DesiredState）。

一条 Peering 是一份"我跟谁建立了 peering 关系"的运维记录；具体落地由其
关联的 ``WgInterface`` / ``BgpSession`` 决定。普通 CRUD 不触发 materialize；
``:provision`` 一键端点在同一事务里把 Peering + WgInterface + BgpSession 一起
建好并物化,避免手工分三步建立时漏配 / 不一致。
"""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.events import EventBus
from ....db.engine import Database
from ....db.models import BgpSession, Node, Peering, WgInterface
from ....services.peering_backfill import backfill_peerings
from ...deps import get_database, get_event_bus
from ._helpers import broadcast_change, materialize_change
from .bgp_sessions import SessionOut, _bgp_out, _validate_bgp_spec
from .interfaces import (
    InterfaceOut,
    _iface_out,
    _validate_iface_spec,
    _validate_wireguard_port_policy,
)

router = APIRouter()


class PeeringIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    remote_asn: int = Field(ge=1)
    remote_node_id: str | None = None
    remote_label: str | None = None
    is_internal: bool = False
    enabled: bool = True
    notes: str | None = None


class PeeringPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    remote_asn: int | None = Field(default=None, ge=1)
    remote_node_id: str | None = None
    remote_label: str | None = None
    is_internal: bool | None = None
    enabled: bool | None = None
    notes: str | None = None


class PeeringOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    local_node_id: str
    remote_node_id: str | None
    name: str
    remote_asn: int
    remote_label: str | None
    is_internal: bool
    enabled: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


def _peering_out(row: Peering) -> PeeringOut:
    return PeeringOut(
        id=row.id,
        local_node_id=row.local_node_id,
        remote_node_id=row.remote_node_id,
        name=row.name,
        remote_asn=row.remote_asn,
        remote_label=row.remote_label,
        is_internal=row.is_internal,
        enabled=row.enabled,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/nodes/{node_id}/peerings", response_model=list[PeeringOut])
async def list_peerings(node_id: str, db: Database = Depends(get_database)) -> list[PeeringOut]:
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        rows = await session.execute(
            select(Peering).where(Peering.local_node_id == node_id).order_by(Peering.name)
        )
        return [_peering_out(row) for row in rows.scalars()]


@router.post(
    "/nodes/{node_id}/peerings",
    response_model=PeeringOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_peering(
    node_id: str,
    payload: PeeringIn,
    db: Database = Depends(get_database),
) -> PeeringOut:
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        if payload.remote_node_id is not None and await session.get(Node, payload.remote_node_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown remote node {payload.remote_node_id}",
            )
        row = Peering(
            local_node_id=node_id,
            remote_node_id=payload.remote_node_id,
            name=payload.name,
            remote_asn=payload.remote_asn,
            remote_label=payload.remote_label,
            is_internal=payload.is_internal,
            enabled=payload.enabled,
            notes=payload.notes,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as exc:  # IntegrityError on UNIQUE(local_node_id, name)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"peering name {payload.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(row)
        return _peering_out(row)


async def _get_peering(session: AsyncSession, peering_id: int) -> Peering:
    row = await session.get(Peering, peering_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown peering {peering_id}")
    return row


@router.get("/peerings/{peering_id}", response_model=PeeringOut)
async def get_peering(peering_id: int, db: Database = Depends(get_database)) -> PeeringOut:
    async with db.session() as session:
        row = await _get_peering(session, peering_id)
        return _peering_out(row)


@router.patch("/peerings/{peering_id}", response_model=PeeringOut)
async def update_peering(
    peering_id: int,
    payload: PeeringPatch,
    db: Database = Depends(get_database),
) -> PeeringOut:
    async with db.session() as session:
        row = await _get_peering(session, peering_id)
        data = payload.model_dump(exclude_unset=True)
        if "remote_node_id" in data and data["remote_node_id"] is not None:
            if await session.get(Node, data["remote_node_id"]) is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"unknown remote node {data['remote_node_id']}",
                )
        for field, value in data.items():
            setattr(row, field, value)
        await session.flush()
        await session.refresh(row)
        return _peering_out(row)


@router.delete("/peerings/{peering_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_peering(peering_id: int, db: Database = Depends(get_database)) -> None:
    async with db.session() as session:
        row = await _get_peering(session, peering_id)
        await session.delete(row)


# ----- 一键化:Peering + WgInterface + BgpSession 同事务建立 -----


class PeeringProvisionIn(BaseModel):
    """一次建立完整 peering 所需的三段:对等元信息 + WG 接口 + 可选 BGP 会话。"""

    model_config = ConfigDict(extra="forbid")

    peering: PeeringIn
    interface_spec: dict[str, Any]
    interface_enabled: bool = True
    interface_sort_order: int = 0
    # 纯传输 peering 可不带 BGP;省略则只建接口。enabled 在 BgpSessionSpec 内。
    bgp_spec: dict[str, Any] | None = None
    bgp_sort_order: int = 0


class PeeringProvisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peering: PeeringOut
    interface: InterfaceOut
    bgp_session: SessionOut | None = None
    generation: int


@router.post(
    "/nodes/{node_id}/peerings/provision",
    response_model=PeeringProvisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def provision_peering(
    node_id: str,
    payload: PeeringProvisionIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> PeeringProvisionOut:
    """在一个事务里建立 Peering + WgInterface +(可选)BgpSession 并物化。

    三者的 spec 先各自走 schema 校验(失败 422);任一行违反唯一约束回 409,
    整个事务回滚——不会留下"建了接口但没建会话"的半成品。接口与会话自动
    挂上新建 Peering 的 id,保证关联一致。
    """

    iface_spec = _validate_iface_spec(payload.interface_spec)
    bgp_spec = _validate_bgp_spec(payload.bgp_spec) if payload.bgp_spec is not None else None
    reason = f"peering {payload.peering.name} provisioned"

    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        if payload.peering.remote_node_id is not None and (
            await session.get(Node, payload.peering.remote_node_id) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown remote node {payload.peering.remote_node_id}",
            )

        peering_row = Peering(
            local_node_id=node_id,
            remote_node_id=payload.peering.remote_node_id,
            name=payload.peering.name,
            remote_asn=payload.peering.remote_asn,
            remote_label=payload.peering.remote_label,
            is_internal=payload.peering.is_internal,
            enabled=payload.peering.enabled,
            notes=payload.peering.notes,
        )
        session.add(peering_row)
        try:
            await session.flush()
        except IntegrityError as exc:  # UNIQUE(local_node_id, name)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"peering name {payload.peering.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(peering_row)

        await _validate_wireguard_port_policy(
            session, node_id=node_id, iface_spec=iface_spec, enabled=payload.interface_enabled
        )
        iface_row = WgInterface(
            node_id=node_id,
            peering_id=peering_row.id,
            enabled=payload.interface_enabled,
            sort_order=payload.interface_sort_order,
        )
        iface_row.apply_spec(iface_spec)
        session.add(iface_row)
        try:
            await session.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"interface {iface_spec.name!r} already exists on node {node_id}",
            ) from exc
        await session.refresh(iface_row)

        bgp_row = None
        if bgp_spec is not None:
            bgp_row = BgpSession(
                node_id=node_id,
                peering_id=peering_row.id,
                sort_order=payload.bgp_sort_order,
            )
            bgp_row.apply_spec(bgp_spec)
            session.add(bgp_row)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"bgp session {bgp_spec.name!r} already exists on node {node_id}",
                ) from exc
            await session.refresh(bgp_row)

        peering_out = _peering_out(peering_row)
        iface_out = _iface_out(iface_row)
        bgp_out = _bgp_out(bgp_row) if bgp_row is not None else None
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return PeeringProvisionOut(
        peering=peering_out,
        interface=iface_out,
        bgp_session=bgp_out,
        generation=state.generation,
    )


# ----- 组合读:peer 作为聚合根,一次拿到 peering + 接口 + 会话 -----


class PeeringFullOut(PeeringOut):
    """peering 元信息 + 其名下全部接口 / BGP 会话。"""

    interfaces: list[InterfaceOut]
    bgp_sessions: list[SessionOut]


def _peering_full_out(row: Peering) -> PeeringFullOut:
    ifaces = sorted(row.wg_interfaces, key=lambda i: (i.sort_order, i.id))
    sessions = sorted(row.bgp_sessions, key=lambda s: (s.sort_order, s.id))
    return PeeringFullOut(
        **_peering_out(row).model_dump(),
        interfaces=[_iface_out(i) for i in ifaces],
        bgp_sessions=[_bgp_out(s) for s in sessions],
    )


@router.get("/nodes/{node_id}/peerings/full", response_model=list[PeeringFullOut])
async def list_peerings_full(
    node_id: str, db: Database = Depends(get_database)
) -> list[PeeringFullOut]:
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        rows = await session.execute(
            select(Peering).where(Peering.local_node_id == node_id).order_by(Peering.name)
        )
        return [_peering_full_out(row) for row in rows.scalars()]


@router.get("/peerings/{peering_id}/full", response_model=PeeringFullOut)
async def get_peering_full(peering_id: int, db: Database = Depends(get_database)) -> PeeringFullOut:
    async with db.session() as session:
        return _peering_full_out(await _get_peering(session, peering_id))


# ----- 全量 create-or-replace:一个 peer 接口建立/更新对等连接所需的全部配置 -----


class FullInterfaceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    enabled: bool = True
    sort_order: int = 0


class FullBgpItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec: dict[str, Any]
    sort_order: int = 0


class PeeringFullIn(BaseModel):
    """一个对等连接的期望完整态:peering 元信息 + 接口集 + BGP 会话集。"""

    model_config = ConfigDict(extra="forbid")

    peering: PeeringIn
    interfaces: list[FullInterfaceItem] = Field(default_factory=list)
    bgp_sessions: list[FullBgpItem] = Field(default_factory=list)


class PeeringFullResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peering: PeeringFullOut
    generation: int


@router.put(
    "/nodes/{node_id}/peerings/full",
    response_model=PeeringFullResult,
    status_code=status.HTTP_200_OK,
)
async def put_peering_full(
    node_id: str,
    payload: PeeringFullIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> PeeringFullResult:
    """按 ``(node_id, peering.name)`` create-or-replace 一个完整 peer。

    语义:peering 元信息 upsert;其子资源**整集替换**——payload 内的接口/会话按
    ``spec.name`` upsert(可纳管同名孤儿/他属资源),该 peering 名下不在 payload 的
    旧子资源删除。单事务:全部 spec 先 schema 校验(失败 422),任一唯一约束冲突回
    409,整笔回滚。改动子表 spec → materialize 新世代并广播。
    """

    # 先把所有 spec 走 schema 校验,任何失败在写库前 422。
    iface_specs = [(_validate_iface_spec(i.spec), i) for i in payload.interfaces]
    bgp_specs = [(_validate_bgp_spec(b.spec), b) for b in payload.bgp_sessions]
    reason = f"peering {payload.peering.name} fully applied"

    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        if payload.peering.remote_node_id is not None and (
            await session.get(Node, payload.peering.remote_node_id) is None
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown remote node {payload.peering.remote_node_id}",
            )

        # 1) upsert Peering(按本节点内唯一名)。
        existing = await session.execute(
            select(Peering).where(
                Peering.local_node_id == node_id, Peering.name == payload.peering.name
            )
        )
        peering_row = existing.scalar_one_or_none()
        if peering_row is None:
            peering_row = Peering(local_node_id=node_id, name=payload.peering.name)
            session.add(peering_row)
        peering_row.remote_node_id = payload.peering.remote_node_id
        peering_row.remote_asn = payload.peering.remote_asn
        peering_row.remote_label = payload.peering.remote_label
        peering_row.is_internal = payload.peering.is_internal
        peering_row.enabled = payload.peering.enabled
        peering_row.notes = payload.peering.notes
        await session.flush()
        peering_id = peering_row.id

        # 2) 接口整集替换。
        await _prune_children(
            session,
            peering_id=peering_id,
            model=WgInterface,
            desired_names={spec.name for spec, _ in iface_specs},
        )
        for spec, item in iface_specs:
            await _validate_wireguard_port_policy(
                session,
                node_id=node_id,
                iface_spec=spec,
                enabled=item.enabled,
                current_iface_id=await _existing_child_id(session, WgInterface, node_id, spec.name),
            )
            await _write_child(
                session,
                model=WgInterface,
                node_id=node_id,
                peering_id=peering_id,
                spec=spec,
                extra_fields={"enabled": item.enabled, "sort_order": item.sort_order},
            )

        # 3) BGP 会话整集替换。
        await _prune_children(
            session,
            peering_id=peering_id,
            model=BgpSession,
            desired_names={spec.name for spec, _ in bgp_specs},
        )
        for spec, item in bgp_specs:
            await _write_child(
                session,
                model=BgpSession,
                node_id=node_id,
                peering_id=peering_id,
                spec=spec,
                extra_fields={"sort_order": item.sort_order},
            )

        await session.flush()
        await session.refresh(peering_row)
        full_out = _peering_full_out(peering_row)
        state = await materialize_change(session, node_id, reason=reason)

    await broadcast_change(bus, node_id, state, reason=reason)
    return PeeringFullResult(peering=full_out, generation=state.generation)


async def _existing_child_id(
    session: AsyncSession, model: type, node_id: str, name: str
) -> int | None:
    """按 ``(node_id, name)`` 查子资源 id（用于端口策略校验时排除自己）；无则 None。"""

    row = await session.execute(
        select(model.id).where(model.node_id == node_id, model.name == name)
    )
    return row.scalar_one_or_none()


async def _prune_children(
    session: AsyncSession,
    *,
    peering_id: int,
    model: type,
    desired_names: set[str],
) -> None:
    """删除该 peering 名下不在 ``desired_names`` 的旧子资源。"""

    rows = await session.execute(select(model).where(model.peering_id == peering_id))
    for row in rows.scalars():
        if row.name not in desired_names:
            await session.delete(row)
    await session.flush()


async def _write_child(
    session: AsyncSession,
    *,
    model: type,
    node_id: str,
    peering_id: int,
    spec: Any,
    extra_fields: dict[str, Any] | None = None,
) -> None:
    """按 ``(node_id, name)`` upsert 一条子资源并挂到 peering 上。

    索引列由 ``row.apply_spec(spec)`` 从校验过的 spec 单源投影（杜绝列/spec 漂移）；
    ``extra_fields`` 只放 spec 之外的列（如接口的 ``enabled``、``sort_order``）。
    """

    name = spec.name
    row = (
        await session.execute(
            select(model).where(model.node_id == node_id, model.name == name)
        )
    ).scalar_one_or_none()
    if row is None:
        row = model(node_id=node_id)
        session.add(row)
    row.peering_id = peering_id
    row.apply_spec(spec)
    for key, value in (extra_fields or {}).items():
        setattr(row, key, value)
    try:
        await session.flush()
    except Exception as exc:  # UNIQUE(node_id, name) 等
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{model.__name__} {name!r} conflict on node {node_id}",
        ) from exc


# ----- 存量回填:把孤儿接口 / 会话归并成 Peering -----


class BackfillIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool = False


class PlannedPeeringOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peering_id: int | None
    name: str
    remote_asn: int
    is_internal: bool
    remote_node_id: str | None
    interface_ids: list[int]
    bgp_session_ids: list[int]


class BackfillOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    created: list[PlannedPeeringOut]
    skipped_interfaces: list[dict[str, Any]]
    skipped_sessions: list[dict[str, Any]]


@router.post("/nodes/{node_id}/peerings/backfill", response_model=BackfillOut)
async def backfill_node_peerings(
    node_id: str,
    payload: BackfillIn,
    db: Database = Depends(get_database),
) -> BackfillOut:
    """把节点上 ``peering_id IS NULL`` 的接口 / 会话按启发式归并成 Peering。

    ``peering_id`` 不进入 DesiredState,故回填**不 materialize、不广播、不推进世代**。
    ``dry_run=true`` 只返回计划不写库。幂等:仅处理孤儿行,重跑无新增。
    """

    async with db.session() as session:
        result = await backfill_peerings(session, node_id, dry_run=payload.dry_run)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        return BackfillOut(
            dry_run=result.dry_run,
            created=[
                PlannedPeeringOut(
                    peering_id=p.peering_id,
                    name=p.name,
                    remote_asn=p.remote_asn,
                    is_internal=p.is_internal,
                    remote_node_id=p.remote_node_id,
                    interface_ids=p.interface_ids,
                    bgp_session_ids=p.bgp_session_ids,
                )
                for p in result.created
            ],
            skipped_interfaces=result.skipped_interfaces,
            skipped_sessions=result.skipped_sessions,
        )


__all__ = ["router"]
