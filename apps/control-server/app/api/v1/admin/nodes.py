from __future__ import annotations

"""Node CRUD + 手动 notify + 世代历史。"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ....core.events import EventBus
from ....db.engine import Database
from ....db.models import Generation, Node
from ....schemas.events import DesiredStateUpdatedEvent, SnapshotRequestEvent
from ....services.desired_state import DesiredStateStore
from ....services.generations import (
    GenerationNotFoundError,
    diff_snapshots,
    get_generation,
    rollback_to_generation,
)
from ...deps import get_database, get_desired_state, get_event_bus
from ._helpers import broadcast_change, materialize_change

router = APIRouter()


class NodeIn(BaseModel):
    """新增节点的请求体。``base_template`` 必须是合法 DesiredState 中非子表的字段集合。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64)
    asn: int = Field(ge=1)
    router_id: str
    site: str | None = None
    loopback_ipv4: str | None = None
    loopback_ipv6: str | None = None
    ipv4_prefixes: list[str] = Field(default_factory=list)
    ipv6_prefixes: list[str] = Field(default_factory=list)
    inventory: dict[str, Any] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    base_template: dict[str, Any] = Field(default_factory=dict)


class NodePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asn: int | None = Field(default=None, ge=1)
    router_id: str | None = None
    site: str | None = None
    loopback_ipv4: str | None = None
    loopback_ipv6: str | None = None
    ipv4_prefixes: list[str] | None = None
    ipv6_prefixes: list[str] | None = None
    inventory: dict[str, Any] | None = None
    labels: dict[str, str] | None = None
    base_template: dict[str, Any] | None = None


class NodeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    asn: int
    router_id: str
    site: str | None
    loopback_ipv4: str | None
    loopback_ipv6: str | None
    ipv4_prefixes: list[str]
    ipv6_prefixes: list[str]
    inventory: dict[str, Any]
    labels: dict[str, str]
    base_template: dict[str, Any]
    current_generation: int
    lifecycle: str
    created_at: datetime
    updated_at: datetime


def _node_out(node: Node) -> NodeOut:
    return NodeOut(
        node_id=node.node_id,
        asn=node.asn,
        router_id=node.router_id,
        site=node.site,
        loopback_ipv4=node.loopback_ipv4,
        loopback_ipv6=node.loopback_ipv6,
        ipv4_prefixes=list(node.ipv4_prefixes or []),
        ipv6_prefixes=list(node.ipv6_prefixes or []),
        inventory=dict(node.inventory or {}),
        labels=dict(node.labels or {}),
        base_template=dict(node.base_template or {}),
        current_generation=node.current_generation,
        lifecycle=node.lifecycle,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


@router.get("/nodes", response_model=list[NodeOut])
async def list_nodes(db: Database = Depends(get_database)) -> list[NodeOut]:
    async with db.session() as session:
        rows = await session.execute(select(Node).order_by(Node.node_id))
        return [_node_out(node) for node in rows.scalars()]


@router.post("/nodes", response_model=NodeOut, status_code=status.HTTP_201_CREATED)
async def create_node(
    payload: NodeIn,
    db: Database = Depends(get_database),
) -> NodeOut:
    async with db.session() as session:
        if await session.get(Node, payload.node_id) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"node {payload.node_id} already exists",
            )
        node = Node(
            node_id=payload.node_id,
            asn=payload.asn,
            router_id=payload.router_id,
            site=payload.site,
            loopback_ipv4=payload.loopback_ipv4,
            loopback_ipv6=payload.loopback_ipv6,
            ipv4_prefixes=list(payload.ipv4_prefixes),
            ipv6_prefixes=list(payload.ipv6_prefixes),
            inventory=dict(payload.inventory),
            labels=dict(payload.labels),
            base_template=dict(payload.base_template),
            current_generation=0,
        )
        session.add(node)
        await session.flush()
        await session.refresh(node)
        return _node_out(node)


@router.get("/nodes/{node_id}", response_model=NodeOut)
async def get_node(node_id: str, db: Database = Depends(get_database)) -> NodeOut:
    async with db.session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        return _node_out(node)


@router.patch("/nodes/{node_id}", response_model=NodeOut)
async def update_node(
    node_id: str,
    payload: NodePatch,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> NodeOut:
    state = None
    async with db.session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")

        data = payload.model_dump(exclude_unset=True)
        for field in ("asn", "router_id", "site", "loopback_ipv4", "loopback_ipv6"):
            if field in data:
                setattr(node, field, data[field])
        for field in ("ipv4_prefixes", "ipv6_prefixes", "inventory", "labels", "base_template"):
            if field in data:
                setattr(node, field, data[field])
        await session.flush()

        # 只有已发布过的节点才在同一事务里重物化；首次创建后还没
        # materialize 的节点等 provision 流程发布第一代。
        # 注意:绝不能在 materialize_change 之后 session.refresh(node)——它会丢弃
        # materialize 刚在内存里推进的 current_generation,导致世代回退、产生
        # 孤儿 generation 行(node 仍指向旧代,agent 永远拉不到新配置)。
        if node.current_generation > 0:
            state = await materialize_change(session, node_id, reason="node updated")
        out = _node_out(node)

    if state is not None:
        await broadcast_change(bus, node_id, state, reason="node updated")
    return out


@router.post("/nodes/{node_id}/decommission", response_model=NodeOut)
async def decommission_node(
    node_id: str,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> NodeOut:
    """退役节点:发布一份"无对端"的 DesiredState(空 interfaces/bgp/dns)。

    agent 收敛即拆除所有隧道、撤掉所有 BGP 会话,节点停止宣告任何路由。子表配置
    保留(可 recommission 恢复),节点 + token 保留以便 agent 拉到退役态并上报。
    确认收敛后再 ``DELETE`` 硬删。已发布过的 active 节点直接 DELETE 会被拒绝
    (见 delete_node),必须先经此端点。
    """

    state = None
    async with db.session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        node.lifecycle = "decommissioned"
        await session.flush()
        # 已发布过才需要下发退役态;从未发布的节点没有部署可拆。
        # 不 refresh:materialize 已在内存推进 current_generation,refresh 会回退它。
        if node.current_generation > 0:
            state = await materialize_change(session, node_id, reason="node decommissioned")
        out = _node_out(node)

    if state is not None:
        await broadcast_change(bus, node_id, state, reason="node decommissioned")
    return out


@router.post("/nodes/{node_id}/recommission", response_model=NodeOut)
async def recommission_node(
    node_id: str,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> NodeOut:
    """撤销退役:恢复 active,重新物化(子表配置原样回到 DesiredState)。"""

    state = None
    async with db.session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        node.lifecycle = "active"
        await session.flush()
        if node.current_generation > 0:
            state = await materialize_change(session, node_id, reason="node recommissioned")
        out = _node_out(node)

    if state is not None:
        await broadcast_change(bus, node_id, state, reason="node recommissioned")
    return out


@router.delete("/nodes/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(node_id: str, db: Database = Depends(get_database)) -> None:
    async with db.session() as session:
        node = await session.get(Node, node_id)
        if node is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        # 防孤儿:已发布过(部署过)的 active 节点必须先退役收敛,否则直接删库会留下
        # 仍在宣告路由、仍架着隧道的节点。从未发布的节点(generation==0)无部署,可直删。
        if node.current_generation > 0 and node.lifecycle != "decommissioned":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"node {node_id} is live; POST /admin/nodes/{node_id}/decommission first "
                    "to tear down its tunnels/BGP, then delete"
                ),
            )
        await session.delete(node)


@router.get("/nodes/{node_id}/desired-state")
async def get_node_desired_state(
    node_id: str,
    desired_state: DesiredStateStore = Depends(get_desired_state),
) -> dict[str, Any]:
    state = await desired_state.get(node_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no desired state for node {node_id}")
    return state.model_dump(mode="json")


class GenerationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generation: int
    reason: str | None
    published_at: datetime


@router.get("/nodes/{node_id}/generations", response_model=list[GenerationOut])
async def list_node_generations(
    node_id: str,
    db: Database = Depends(get_database),
    limit: int = 50,
) -> list[GenerationOut]:
    if limit <= 0 or limit > 500:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limit must be in 1..500")
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        rows = await session.execute(
            select(Generation)
            .where(Generation.node_id == node_id)
            .order_by(Generation.generation.desc())
            .limit(limit)
        )
        return [
            GenerationOut(
                generation=row.generation,
                reason=row.reason,
                published_at=row.published_at,
            )
            for row in rows.scalars()
        ]


class GenerationDetailOut(GenerationOut):
    """单代详情：元信息 + 完整 DesiredState 快照。"""

    snapshot: dict[str, Any]


@router.get("/nodes/{node_id}/generations/{generation}", response_model=GenerationDetailOut)
async def get_node_generation(
    node_id: str,
    generation: int,
    db: Database = Depends(get_database),
) -> GenerationDetailOut:
    """读取某一代的完整快照（运维查看历史下发内容）。"""

    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        row = await get_generation(session, node_id, generation)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"node {node_id} has no generation {generation}",
            )
        return GenerationDetailOut(
            generation=row.generation,
            reason=row.reason,
            published_at=row.published_at,
            snapshot=dict(row.snapshot),
        )


class GenerationDiffOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    from_generation: int
    to_generation: int
    changed: bool
    changes: list[dict[str, Any]]


@router.get("/nodes/{node_id}/generations/{generation}/diff", response_model=GenerationDiffOut)
async def diff_node_generation(
    node_id: str,
    generation: int,
    db: Database = Depends(get_database),
    against: int | None = None,
) -> GenerationDiffOut:
    """对比两代快照，产出字段级变更列表。

    ``against`` 是对比基准（"从哪一代变到 ``generation``"），缺省取
    ``generation - 1``。第 1 代没有上一代时必须显式传 ``against``。
    """

    base = against if against is not None else generation - 1
    if base < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="no previous generation to diff against; pass ?against=<generation>",
        )
    async with db.session() as session:
        if await session.get(Node, node_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")
        new_row = await get_generation(session, node_id, generation)
        old_row = await get_generation(session, node_id, base)
        for gen, row in ((generation, new_row), (base, old_row)):
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"node {node_id} has no generation {gen}",
                )
        assert old_row is not None and new_row is not None
        changes = diff_snapshots(old_row.snapshot, new_row.snapshot)
    return GenerationDiffOut(
        node_id=node_id,
        from_generation=base,
        to_generation=generation,
        changed=bool(changes),
        changes=changes,
    )


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class RollbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    target_generation: int
    new_generation: int
    reason: str
    subscribers: int
    delivered: int


@router.post("/nodes/{node_id}/generations/{generation}/rollback", response_model=RollbackResponse)
async def rollback_node_generation(
    node_id: str,
    generation: int,
    payload: RollbackRequest | None = None,
    db: Database = Depends(get_database),
    bus: EventBus = Depends(get_event_bus),
) -> RollbackResponse:
    """把 ``generation`` 的快照重新发布为新一代并广播。

    注意:回滚只重放快照,不回退 normalized 子表;后续任何触发 materialize 的
    管理写入都会覆盖这次回滚(详见 services/generations.py)。
    """

    payload = payload or RollbackRequest()
    reason = payload.reason or f"rollback to generation {generation}"
    state = None
    async with db.session() as session:
        try:
            state = await rollback_to_generation(session, node_id, generation, reason=reason)
        except GenerationNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"node {node_id} has no generation {generation}",
            ) from exc
        if state is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}"
            )

    stats = await broadcast_change(bus, node_id, state, reason=reason)
    return RollbackResponse(
        node_id=node_id,
        target_generation=generation,
        new_generation=stats["generation"],
        reason=reason,
        subscribers=stats["subscribers"],
        delivered=stats["delivered"],
    )


class NotifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: Literal["desired_state_updated", "snapshot_request"] = "desired_state_updated"
    reason: str | None = None


class NotifyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    event: str
    generation: int | None
    subscribers: int
    delivered: int


@router.post("/nodes/{node_id}/notify", response_model=NotifyResponse)
async def notify_node(
    node_id: str,
    payload: NotifyRequest | None = None,
    desired_state: DesiredStateStore = Depends(get_desired_state),
    bus: EventBus = Depends(get_event_bus),
) -> NotifyResponse:
    payload = payload or NotifyRequest()
    state = await desired_state.get(node_id)
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown node {node_id}")

    # notify 是纯实时门铃:没有在线 agent 订阅时,事件无人接收。拒绝下发(避免
    # desired_state_updated 白白递增世代、snapshot_request 石沉大海)。节点失联时
    # 配置更改仍由 CRUD 持久化,agent 重连后经周期对账拉取——无需手动门铃。
    if bus.subscriber_count(node_id) == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"node {node_id} has no live agent connection; real-time event "
                f"{payload.event!r} refused. The node appears disconnected from the "
                "control plane. Config changes still persist and are applied on the "
                "agent's next reconcile."
            ),
        )

    if payload.event == "desired_state_updated":
        bumped = await desired_state.bump(node_id, reason=payload.reason or "manual bump")
        assert bumped is not None
        event = DesiredStateUpdatedEvent(
            generation=bumped.generation, reason=payload.reason or "manual bump"
        )
        generation = bumped.generation
    else:
        event = SnapshotRequestEvent(reason=payload.reason)
        generation = state.generation

    delivered = await bus.publish(node_id, event.model_dump(mode="json"))
    return NotifyResponse(
        node_id=node_id,
        event=payload.event,
        generation=generation,
        subscribers=bus.subscriber_count(node_id),
        delivered=delivered,
    )


__all__ = ["router"]
