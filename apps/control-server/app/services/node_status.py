from __future__ import annotations

"""节点运行时健康仓库 ``NodeStatusStore``。

把 agent 上报的三类 payload（RuntimeSnapshot / ReconciliationReport / ApplyResult）
持久化到 ``node_status``（每节点最新）+ ``node_status_events``（append-only 历史），
并派生出节点健康 ``health`` ∈ {ok, degraded, stale, down, unknown}。

健康派生规则（以最近一次 report / apply 为准）：
- 任一为 ``failed`` 或存在 drift / 任一为 ``degraded`` → ``degraded``
- desired_generation 与 observed_generation 已知且不一致 → ``stale``
- 否则 → ``ok``；从未上报过 report/apply → ``unknown``
另外读取侧基于 ``updated_at`` 的时间做"失联"覆盖：超过 ``stale_after_seconds``
未上报的 ok 节点降为 ``stale``；超过更长的 ``down_after_seconds`` 完全没上报则覆盖为
``down``（宕机），无论库里存的是什么状态（``unknown`` 除外）。
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select

from dn42_schemas import (
    ApplyResult,
    ApplyStatus,
    NodeHealth,
    ReconciliationReport,
    RuntimeSnapshot,
)

from ..db.engine import Database
from ..db.models import NodeStatus, NodeStatusEvent

# 每个节点 / 每种 kind 在历史表里最多保留多少条。
_HISTORY_KEEP = 100


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _derive_health(
    *,
    report_status: str | None,
    apply_status: str | None,
    drift_count: int,
    observed_generation: int | None,
    desired_generation: int | None,
) -> NodeHealth:
    # report/apply 状态以字符串存库,这里用 ApplyStatus 枚举值做桥接,避免硬编码
    # 字面量与 ApplyStatus 漂移。
    if report_status is None and apply_status is None:
        return NodeHealth.UNKNOWN
    bad = {ApplyStatus.FAILED.value}
    soft = {ApplyStatus.DEGRADED.value}
    if report_status in bad or apply_status in bad:
        return NodeHealth.DEGRADED
    if drift_count > 0:
        return NodeHealth.DEGRADED
    if report_status in soft or apply_status in soft:
        return NodeHealth.DEGRADED
    if (
        desired_generation is not None
        and observed_generation is not None
        and observed_generation != desired_generation
    ):
        return NodeHealth.STALE
    return NodeHealth.OK


class NodeStatusStore:
    """读写节点运行时健康。所有方法各自开一个 session。

    ``stale_after_seconds`` / ``down_after_seconds`` 是控制面视角的失联阈值:
    超过前者未上报的 ok 节点读出时降为 ``stale``;超过后者完全没上报则覆盖为
    ``down``(宕机),无论库里存的是什么状态。两者来自配置,可被方法参数覆盖。
    """

    def __init__(
        self,
        database: Database,
        *,
        stale_after_seconds: float = 900.0,
        down_after_seconds: float = 3600.0,
    ) -> None:
        self._db = database
        self._stale_after = stale_after_seconds
        self._down_after = down_after_seconds

    async def _get_or_create(self, session, node_id: str) -> NodeStatus:
        row = await session.get(NodeStatus, node_id)
        if row is None:
            row = NodeStatus(node_id=node_id)
            session.add(row)
        return row

    async def _trim_history(self, session, node_id: str, kind: str) -> None:
        keep_ids = (
            select(NodeStatusEvent.id)
            .where(NodeStatusEvent.node_id == node_id, NodeStatusEvent.kind == kind)
            .order_by(NodeStatusEvent.id.desc())
            .limit(_HISTORY_KEEP)
        )
        await session.execute(
            delete(NodeStatusEvent).where(
                NodeStatusEvent.node_id == node_id,
                NodeStatusEvent.kind == kind,
                NodeStatusEvent.id.notin_(keep_ids),
            )
        )

    async def _recompute_health(self, row: NodeStatus) -> None:
        row.health = _derive_health(
            report_status=row.last_report_status,
            apply_status=row.last_apply_status,
            drift_count=row.drift_count,
            observed_generation=row.observed_generation,
            desired_generation=row.desired_generation,
        ).value

    async def record_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        payload = snapshot.model_dump(mode="json")
        async with self._db.session() as session:
            row = await self._get_or_create(session, snapshot.node_id)
            row.last_snapshot = payload
            row.last_snapshot_at = _parse_ts(snapshot.captured_at)
            if snapshot.generation is not None:
                row.observed_generation = snapshot.generation
            session.add(
                NodeStatusEvent(
                    node_id=snapshot.node_id,
                    kind="snapshot",
                    generation=snapshot.generation,
                    status=None,
                    payload=payload,
                )
            )
            await self._recompute_health(row)
            await self._trim_history(session, snapshot.node_id, "snapshot")

    async def record_report(self, report: ReconciliationReport) -> None:
        payload = report.model_dump(mode="json")
        async with self._db.session() as session:
            row = await self._get_or_create(session, report.node_id)
            row.last_report = payload
            row.last_report_status = str(report.status.value)
            row.last_report_at = _parse_ts(report.captured_at)
            row.desired_generation = report.desired_generation
            row.observed_generation = report.observed_generation
            row.drift_count = len(report.drift)
            session.add(
                NodeStatusEvent(
                    node_id=report.node_id,
                    kind="report",
                    generation=report.desired_generation,
                    status=str(report.status.value),
                    payload=payload,
                )
            )
            await self._recompute_health(row)
            await self._trim_history(session, report.node_id, "report")

    async def record_apply(self, result: ApplyResult) -> None:
        payload = result.model_dump(mode="json")
        async with self._db.session() as session:
            row = await self._get_or_create(session, result.node_id)
            row.last_apply = payload
            row.last_apply_status = str(result.status.value)
            row.last_apply_at = _parse_ts(result.finished_at or result.started_at)
            row.desired_generation = result.generation
            session.add(
                NodeStatusEvent(
                    node_id=result.node_id,
                    kind="apply",
                    generation=result.generation,
                    status=str(result.status.value),
                    payload=payload,
                )
            )
            await self._recompute_health(row)
            await self._trim_history(session, result.node_id, "apply")

    @staticmethod
    def _row_to_dict(
        row: NodeStatus, *, stale_after_seconds: float, down_after_seconds: float
    ) -> dict:
        health = row.health
        updated = row.updated_at
        if updated is not None and health != NodeHealth.UNKNOWN.value:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - updated).total_seconds()
            # 长时间彻底失联 → 宕机,覆盖任何已知状态(ok/stale/degraded)。
            if age > down_after_seconds:
                health = NodeHealth.DOWN.value
            # 短时间未上报且原本健康 → 落后。
            elif health == NodeHealth.OK.value and age > stale_after_seconds:
                health = NodeHealth.STALE.value
        return {
            "node_id": row.node_id,
            "health": health,
            "desired_generation": row.desired_generation,
            "observed_generation": row.observed_generation,
            "last_report_status": row.last_report_status,
            "last_apply_status": row.last_apply_status,
            "drift_count": row.drift_count,
            "last_snapshot_at": row.last_snapshot_at.isoformat() if row.last_snapshot_at else None,
            "last_report_at": row.last_report_at.isoformat() if row.last_report_at else None,
            "last_apply_at": row.last_apply_at.isoformat() if row.last_apply_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    async def get(
        self,
        node_id: str,
        *,
        stale_after_seconds: float | None = None,
        down_after_seconds: float | None = None,
    ) -> dict | None:
        async with self._db.session() as session:
            row = await session.get(NodeStatus, node_id)
            if row is None:
                return None
            data = self._row_to_dict(
                row,
                stale_after_seconds=stale_after_seconds
                if stale_after_seconds is not None
                else self._stale_after,
                down_after_seconds=down_after_seconds
                if down_after_seconds is not None
                else self._down_after,
            )
            data["last_snapshot"] = row.last_snapshot
            data["last_report"] = row.last_report
            data["last_apply"] = row.last_apply
            return data

    async def list_all(
        self,
        *,
        stale_after_seconds: float | None = None,
        down_after_seconds: float | None = None,
    ) -> list[dict]:
        stale = stale_after_seconds if stale_after_seconds is not None else self._stale_after
        down = down_after_seconds if down_after_seconds is not None else self._down_after
        async with self._db.session() as session:
            rows = await session.execute(select(NodeStatus).order_by(NodeStatus.node_id))
            return [
                self._row_to_dict(row, stale_after_seconds=stale, down_after_seconds=down)
                for row in rows.scalars()
            ]

    async def list_events(
        self, node_id: str, *, kind: str | None = None, limit: int = 50
    ) -> list[dict]:
        async with self._db.session() as session:
            stmt = select(NodeStatusEvent).where(NodeStatusEvent.node_id == node_id)
            if kind is not None:
                stmt = stmt.where(NodeStatusEvent.kind == kind)
            stmt = stmt.order_by(NodeStatusEvent.id.desc()).limit(limit)
            rows = await session.execute(stmt)
            return [
                {
                    "id": ev.id,
                    "kind": ev.kind,
                    "generation": ev.generation,
                    "status": ev.status,
                    "created_at": ev.created_at.isoformat() if ev.created_at else None,
                    "payload": ev.payload,
                }
                for ev in rows.scalars()
            ]


__all__ = ["NodeStatusStore"]
