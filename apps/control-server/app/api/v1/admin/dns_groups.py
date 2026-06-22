from __future__ import annotations

"""共享 DNS 组的管理 API（记录为中心，取代旧的节点级 DnsZone）。

三级：``DnsGroup``（name + bind_addresses）→ ``DnsGroupZone``（组声明的权威 zone + 可选 SOA
覆盖）→ ``DnsRecord``（扁平记录 name/type/content/ttl/comment）。节点经 ``Node.dns_group_id``
订阅——分配组即启用 DNS；多个节点订阅同一组 ⇒ 相同配置 ⇒ anycast / 任拨。rDNS 就是反向 zone
下 ``type=PTR`` 的记录。

写纪律遵守 ``_helpers``：CRUD + materialize 同事务（失败整体回滚），提交后才 broadcast。
**组 / zone / 记录变更会重新物化该组的全部成员节点**（这正是多节点 DNS 同步的机制）。
"""

from datetime import datetime
from typing import Any

from dn42_common import validate_domain_name
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select, update

from dn42_schemas import DnsRecordSpec, DnsSpec

from ....core.events import EventBus
from ....db.engine import Database
from ....db.models import DnsGroup, DnsGroupZone, DnsRecord, Node
from ...deps import get_database, get_event_bus
from ._helpers import _errors_to_json, broadcast_change, materialize_change

router = APIRouter()


# ---- DTO --------------------------------------------------------------------


class DnsGroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    bind_addresses: list[str] = []
    cache_ttl_seconds: int = 300
    forwards: list[dict[str, Any]] = []
    enabled: bool = True


class DnsGroupPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    bind_addresses: list[str] | None = None
    cache_ttl_seconds: int | None = None
    forwards: list[dict[str, Any]] | None = None
    enabled: bool | None = None


class DnsGroupOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    bind_addresses: list[str]
    cache_ttl_seconds: int
    forwards: list[dict[str, Any]]
    enabled: bool
    zone_count: int
    member_count: int
    created_at: datetime
    updated_at: datetime


class DnsZoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone: str
    primary_ns: str | None = None
    admin_email: str | None = None
    soa_refresh: int | None = None
    soa_retry: int | None = None
    soa_expire: int | None = None
    soa_minimum: int | None = None
    default_ttl: int | None = None
    enabled: bool = True


class DnsZonePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone: str | None = None
    primary_ns: str | None = None
    admin_email: str | None = None
    soa_refresh: int | None = None
    soa_retry: int | None = None
    soa_expire: int | None = None
    soa_minimum: int | None = None
    default_ttl: int | None = None
    enabled: bool | None = None


class DnsZoneOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    dns_group_id: int
    zone: str
    primary_ns: str | None
    admin_email: str | None
    soa_refresh: int | None
    soa_retry: int | None
    soa_expire: int | None
    soa_minimum: int | None
    default_ttl: int | None
    enabled: bool
    record_count: int
    created_at: datetime
    updated_at: datetime


class DnsRecordIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    content: str
    ttl: int | None = None
    comment: str | None = None
    enabled: bool = True
    sort_order: int = 0


class DnsRecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    type: str | None = None
    content: str | None = None
    ttl: int | None = None
    comment: str | None = None
    enabled: bool | None = None
    sort_order: int | None = None


class DnsRecordOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    dns_group_zone_id: int
    name: str
    type: str
    content: str
    ttl: int | None
    comment: str | None
    enabled: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class DnsGroupAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dns_group_id: int | None


# ---- validation / serialization --------------------------------------------


def _validate_group_config(
    bind_addresses: list[str], cache_ttl_seconds: int, forwards: list[dict[str, Any]]
) -> DnsSpec:
    try:
        return DnsSpec.model_validate(
            {
                "enabled": True,
                "bind_addresses": bind_addresses,
                "cache_ttl_seconds": cache_ttl_seconds,
                "zones": [],
                "forwards": forwards,
            }
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "invalid DNS group config", "errors": _errors_to_json(exc)},
        ) from exc


def _validate_zone_name(zone: str) -> str:
    try:
        # allow_slash：放行 RFC 2317 无类反向委派 zone（0/26.0.20.172.in-addr.arpa）。
        return validate_domain_name(zone, require_multi_label=False, allow_slash=True)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": f"invalid zone name: {exc}"},
        ) from exc


def _validate_record(name: str, type_: str, content: str, ttl: int | None) -> DnsRecordSpec:
    try:
        return DnsRecordSpec.model_validate(
            {"name": name, "type": type_, "value": content, "ttl": ttl}
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "invalid DNS record", "errors": _errors_to_json(exc)},
        ) from exc


async def _group_out(session: Any, group: DnsGroup) -> DnsGroupOut:
    zone_count = await session.scalar(
        select(func.count()).select_from(DnsGroupZone).where(DnsGroupZone.dns_group_id == group.id)
    )
    member_count = await session.scalar(
        select(func.count()).select_from(Node).where(Node.dns_group_id == group.id)
    )
    return DnsGroupOut(
        id=group.id,
        name=group.name,
        bind_addresses=list(group.bind_addresses or []),
        cache_ttl_seconds=group.cache_ttl_seconds,
        forwards=list(group.forwards or []),
        enabled=group.enabled,
        zone_count=zone_count or 0,
        member_count=member_count or 0,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def _zone_out(session: Any, zone: DnsGroupZone) -> DnsZoneOut:
    record_count = await session.scalar(
        select(func.count()).select_from(DnsRecord).where(DnsRecord.dns_group_zone_id == zone.id)
    )
    return DnsZoneOut(
        id=zone.id,
        dns_group_id=zone.dns_group_id,
        zone=zone.zone,
        primary_ns=zone.primary_ns,
        admin_email=zone.admin_email,
        soa_refresh=zone.soa_refresh,
        soa_retry=zone.soa_retry,
        soa_expire=zone.soa_expire,
        soa_minimum=zone.soa_minimum,
        default_ttl=zone.default_ttl,
        enabled=zone.enabled,
        record_count=record_count or 0,
        created_at=zone.created_at,
        updated_at=zone.updated_at,
    )


def _record_out(row: DnsRecord) -> DnsRecordOut:
    return DnsRecordOut(
        id=row.id,
        dns_group_zone_id=row.dns_group_zone_id,
        name=row.name,
        type=row.type,
        content=row.content,
        ttl=row.ttl,
        comment=row.comment,
        enabled=row.enabled,
        sort_order=row.sort_order,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _get_group(session: Any, group_id: int) -> DnsGroup:
    group = await session.get(DnsGroup, group_id)
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown dns group {group_id}"
        )
    return group


async def _get_zone(session: Any, group_id: int, zone_id: int) -> DnsGroupZone:
    zone = await session.get(DnsGroupZone, zone_id)
    if zone is None or zone.dns_group_id != group_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown dns zone {zone_id} in group {group_id}",
        )
    return zone


async def _get_record(session: Any, zone_id: int, record_id: int) -> DnsRecord:
    row = await session.get(DnsRecord, record_id)
    if row is None or row.dns_group_zone_id != zone_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown dns record {record_id} in zone {zone_id}",
        )
    return row


async def _member_ids(session: Any, group_id: int) -> list[str]:
    rows = await session.execute(select(Node.node_id).where(Node.dns_group_id == group_id))
    return [node_id for (node_id,) in rows.all()]


async def _rematerialize(session: Any, node_ids: list[str], reason: str) -> list[tuple[str, Any]]:
    return [
        (node_id, await materialize_change(session, node_id, reason=reason)) for node_id in node_ids
    ]


async def _broadcast_all(bus: EventBus, rematerialized: list[tuple[str, Any]], reason: str) -> None:
    for node_id, state in rematerialized:
        await broadcast_change(bus, node_id, state, reason=reason)


# ---- group CRUD -------------------------------------------------------------


@router.get("/dns-groups", response_model=list[DnsGroupOut])
async def list_groups(db: Database = Depends(get_database)) -> list[DnsGroupOut]:
    async with db.session() as session:
        rows = await session.execute(select(DnsGroup).order_by(DnsGroup.name))
        return [await _group_out(session, group) for group in rows.scalars()]


@router.post("/dns-groups", response_model=DnsGroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(payload: DnsGroupIn, db: Database = Depends(get_database)) -> DnsGroupOut:
    cfg = _validate_group_config(
        payload.bind_addresses, payload.cache_ttl_seconds, payload.forwards
    )
    async with db.session() as session:
        group = DnsGroup(
            name=payload.name,
            bind_addresses=list(cfg.bind_addresses),
            cache_ttl_seconds=cfg.cache_ttl_seconds,
            forwards=[f.model_dump(mode="json") for f in cfg.forwards],
            enabled=payload.enabled,
        )
        session.add(group)
        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"dns group {payload.name!r} already exists",
            ) from exc
        await session.refresh(group)
        return await _group_out(session, group)


@router.get("/dns-groups/{group_id}", response_model=DnsGroupOut)
async def get_group(group_id: int, db: Database = Depends(get_database)) -> DnsGroupOut:
    async with db.session() as session:
        return await _group_out(session, await _get_group(session, group_id))


@router.patch("/dns-groups/{group_id}", response_model=DnsGroupOut)
async def update_group(
    group_id: int,
    payload: DnsGroupPatch,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> DnsGroupOut:
    async with db.session() as session:
        group = await _get_group(session, group_id)
        bind = (
            payload.bind_addresses
            if payload.bind_addresses is not None
            else list(group.bind_addresses or [])
        )
        ttl = (
            payload.cache_ttl_seconds
            if payload.cache_ttl_seconds is not None
            else group.cache_ttl_seconds
        )
        fwd = payload.forwards if payload.forwards is not None else list(group.forwards or [])
        cfg = _validate_group_config(bind, ttl, fwd)
        if payload.name is not None:
            group.name = payload.name
        group.bind_addresses = list(cfg.bind_addresses)
        group.cache_ttl_seconds = cfg.cache_ttl_seconds
        group.forwards = [f.model_dump(mode="json") for f in cfg.forwards]
        if payload.enabled is not None:
            group.enabled = payload.enabled
        reason = f"dns group {group.name} updated"
        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"dns group {group.name!r} already exists",
            ) from exc
        rematerialized = await _rematerialize(session, await _member_ids(session, group_id), reason)
        out = await _group_out(session, group)

    await _broadcast_all(bus, rematerialized, reason)
    return out


@router.delete("/dns-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: int,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> None:
    async with db.session() as session:
        group = await _get_group(session, group_id)
        reason = f"dns group {group.name} deleted"
        member_ids = await _member_ids(session, group_id)
        await session.execute(
            update(Node).where(Node.dns_group_id == group_id).values(dns_group_id=None)
        )
        await session.delete(group)
        await session.flush()
        rematerialized = await _rematerialize(session, member_ids, reason)

    await _broadcast_all(bus, rematerialized, reason)


# ---- zone CRUD（组声明的权威 zone）------------------------------------------


@router.get("/dns-groups/{group_id}/zones", response_model=list[DnsZoneOut])
async def list_zones(group_id: int, db: Database = Depends(get_database)) -> list[DnsZoneOut]:
    async with db.session() as session:
        await _get_group(session, group_id)
        rows = await session.execute(
            select(DnsGroupZone)
            .where(DnsGroupZone.dns_group_id == group_id)
            .order_by(DnsGroupZone.zone)
        )
        return [await _zone_out(session, zone) for zone in rows.scalars()]


def _zone_columns(payload: DnsZoneIn | DnsZonePatch) -> dict[str, Any]:
    return {
        "primary_ns": payload.primary_ns,
        "admin_email": payload.admin_email,
        "soa_refresh": payload.soa_refresh,
        "soa_retry": payload.soa_retry,
        "soa_expire": payload.soa_expire,
        "soa_minimum": payload.soa_minimum,
        "default_ttl": payload.default_ttl,
    }


@router.post(
    "/dns-groups/{group_id}/zones",
    response_model=DnsZoneOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_zone(
    group_id: int,
    payload: DnsZoneIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> DnsZoneOut:
    zone_name = _validate_zone_name(payload.zone)
    reason = f"dns zone {zone_name} added"
    async with db.session() as session:
        await _get_group(session, group_id)
        zone = DnsGroupZone(
            dns_group_id=group_id, zone=zone_name, enabled=payload.enabled, **_zone_columns(payload)
        )
        session.add(zone)
        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"dns zone {zone_name!r} already exists in group {group_id}",
            ) from exc
        await session.refresh(zone)
        out = await _zone_out(session, zone)
        rematerialized = await _rematerialize(session, await _member_ids(session, group_id), reason)

    await _broadcast_all(bus, rematerialized, reason)
    return out


@router.patch("/dns-groups/{group_id}/zones/{zone_id}", response_model=DnsZoneOut)
async def update_zone(
    group_id: int,
    zone_id: int,
    payload: DnsZonePatch,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> DnsZoneOut:
    async with db.session() as session:
        zone = await _get_zone(session, group_id, zone_id)
        if payload.zone is not None:
            zone.zone = _validate_zone_name(payload.zone)
        for key, value in _zone_columns(payload).items():
            if value is not None:
                setattr(zone, key, value)
        if payload.enabled is not None:
            zone.enabled = payload.enabled
        try:
            await session.flush()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"dns zone {zone.zone!r} already exists in group {group_id}",
            ) from exc
        await session.refresh(zone)
        out = await _zone_out(session, zone)
        reason = f"dns zone {zone.zone} updated"
        rematerialized = await _rematerialize(session, await _member_ids(session, group_id), reason)

    await _broadcast_all(bus, rematerialized, reason)
    return out


@router.delete("/dns-groups/{group_id}/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_zone(
    group_id: int,
    zone_id: int,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> None:
    async with db.session() as session:
        zone = await _get_zone(session, group_id, zone_id)
        reason = f"dns zone {zone.zone} deleted"
        await session.delete(zone)
        await session.flush()
        rematerialized = await _rematerialize(session, await _member_ids(session, group_id), reason)

    await _broadcast_all(bus, rematerialized, reason)


# ---- record CRUD（扁平记录）-------------------------------------------------


@router.get("/dns-groups/{group_id}/zones/{zone_id}/records", response_model=list[DnsRecordOut])
async def list_records(
    group_id: int, zone_id: int, db: Database = Depends(get_database)
) -> list[DnsRecordOut]:
    async with db.session() as session:
        await _get_zone(session, group_id, zone_id)
        rows = await session.execute(
            select(DnsRecord)
            .where(DnsRecord.dns_group_zone_id == zone_id)
            .order_by(DnsRecord.sort_order, DnsRecord.id)
        )
        return [_record_out(row) for row in rows.scalars()]


@router.post(
    "/dns-groups/{group_id}/zones/{zone_id}/records",
    response_model=DnsRecordOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_record(
    group_id: int,
    zone_id: int,
    payload: DnsRecordIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> DnsRecordOut:
    spec = _validate_record(payload.name, payload.type, payload.content, payload.ttl)
    reason = f"dns record {spec.name} {spec.type} added"
    async with db.session() as session:
        await _get_zone(session, group_id, zone_id)
        row = DnsRecord(
            dns_group_zone_id=zone_id,
            name=spec.name,
            type=spec.type,
            content=payload.content,
            ttl=payload.ttl,
            comment=payload.comment,
            enabled=payload.enabled,
            sort_order=payload.sort_order,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        out = _record_out(row)
        rematerialized = await _rematerialize(session, await _member_ids(session, group_id), reason)

    await _broadcast_all(bus, rematerialized, reason)
    return out


@router.patch(
    "/dns-groups/{group_id}/zones/{zone_id}/records/{record_id}", response_model=DnsRecordOut
)
async def update_record(
    group_id: int,
    zone_id: int,
    record_id: int,
    payload: DnsRecordPatch,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> DnsRecordOut:
    async with db.session() as session:
        await _get_group(session, group_id)
        row = await _get_record(session, zone_id, record_id)
        name = payload.name if payload.name is not None else row.name
        type_ = payload.type if payload.type is not None else row.type
        content = payload.content if payload.content is not None else row.content
        ttl = payload.ttl if payload.ttl is not None else row.ttl
        spec = _validate_record(name, type_, content, ttl)
        row.name = spec.name
        row.type = spec.type
        row.content = content
        if payload.ttl is not None:
            row.ttl = payload.ttl
        if payload.comment is not None:
            row.comment = payload.comment
        if payload.enabled is not None:
            row.enabled = payload.enabled
        if payload.sort_order is not None:
            row.sort_order = payload.sort_order
        await session.flush()
        await session.refresh(row)
        out = _record_out(row)
        reason = f"dns record {row.name} {row.type} updated"
        rematerialized = await _rematerialize(session, await _member_ids(session, group_id), reason)

    await _broadcast_all(bus, rematerialized, reason)
    return out


@router.delete(
    "/dns-groups/{group_id}/zones/{zone_id}/records/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_record(
    group_id: int,
    zone_id: int,
    record_id: int,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> None:
    async with db.session() as session:
        await _get_group(session, group_id)
        row = await _get_record(session, zone_id, record_id)
        reason = f"dns record {row.name} {row.type} deleted"
        await session.delete(row)
        await session.flush()
        rematerialized = await _rematerialize(session, await _member_ids(session, group_id), reason)

    await _broadcast_all(bus, rematerialized, reason)


# ---- node assignment --------------------------------------------------------


@router.put("/nodes/{node_id}/dns-group", response_model=DnsGroupOut | None)
async def assign_node_group(
    node_id: str,
    payload: DnsGroupAssignIn,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> DnsGroupOut | None:
    """给节点分配 / 取消（dns_group_id=null）DNS 组——分配即启用，取消即停 DNS。"""

    async with db.session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}"
            )
        group: DnsGroup | None = None
        if payload.dns_group_id is not None:
            group = await _get_group(session, payload.dns_group_id)
        node.dns_group_id = payload.dns_group_id
        reason = f"assigned dns group {group.name}" if group is not None else "unassigned dns group"
        await session.flush()
        state = await materialize_change(session, node_id, reason=reason)
        out = await _group_out(session, group) if group is not None else None

    await broadcast_change(bus, node_id, state, reason=reason)
    return out


__all__ = ["router"]
