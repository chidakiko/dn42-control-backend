"""strip build.context/build.dockerfile from stored DesiredState JSON

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-12 01:00:00.000000

router 镜像改为 agent 内存生成 Dockerfile + Engine API ``fileobj`` 构建，
``BuildSpec`` 删除了 ``context`` / ``dockerfile`` 字段。strict schema 下
存量 ``nodes.base_template`` 与 ``generations.snapshot`` 里的残留键会让
重新校验失败，需在数据层剥离。downgrade 补回历史默认值。

注意：此变更会改变带 build 服务的 ``service_config_hash``，升级后每个
节点的第一轮 reconcile 会把 router 容器组重建一次（一次性 BGP 抖动）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TARGETS = (
    ("nodes", "node_id", "base_template"),
    ("generations", "id", "snapshot"),
)


def _services(payload: dict) -> list[dict]:
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        return []
    services = runtime.get("services")
    if not isinstance(services, list):
        return []
    return [item for item in services if isinstance(item, dict)]


def _strip(payload: dict) -> bool:
    changed = False
    for service in _services(payload):
        build = service.get("build")
        if not isinstance(build, dict):
            continue
        for legacy in ("context", "dockerfile"):
            if legacy in build:
                build.pop(legacy)
                changed = True
    return changed


def _restore(payload: dict) -> bool:
    changed = False
    for service in _services(payload):
        build = service.get("build")
        if not isinstance(build, dict):
            continue
        if "context" not in build:
            build["context"] = "."
            changed = True
        if "dockerfile" not in build:
            build["dockerfile"] = "docker/router/Dockerfile"
            changed = True
    return changed


def _rewrite(transform) -> None:
    connection = op.get_bind()
    for table, pk, column in _TARGETS:
        rows = connection.execute(
            sa.text(f"SELECT {pk}, {column} FROM {table}")  # noqa: S608 - 常量表名
        ).fetchall()
        for key, raw in rows:
            payload = raw if isinstance(raw, dict) else json.loads(raw) if raw else None
            if not isinstance(payload, dict):
                continue
            if not transform(payload):
                continue
            connection.execute(
                sa.text(f"UPDATE {table} SET {column} = :payload WHERE {pk} = :key"),  # noqa: S608
                {"payload": json.dumps(payload, ensure_ascii=False), "key": key},
            )


def upgrade() -> None:
    _rewrite(_strip)


def downgrade() -> None:
    _rewrite(_restore)
