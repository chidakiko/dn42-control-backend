"""strip docker-compose era fields from stored DesiredState JSON

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-12 00:00:00.000000

去 docker-compose 化：schema 删除了 ``runtime.adapter``、``templates.compose``、
``templates.systemd``（``templates.docker`` 取代 compose 槽位）。``DesiredState``
是 ``extra="forbid"`` 的严格模型，存量 ``nodes.base_template`` 与
``generations.snapshot`` JSON 里残留这些键会让重新校验直接失败，因此必须
在数据层剥离。downgrade 仅把键补回默认值（历史信息本来就只有默认值）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TARGETS = (
    ("nodes", "node_id", "base_template"),
    ("generations", "id", "snapshot"),
)


def _strip(payload: dict) -> bool:
    changed = False
    runtime = payload.get("runtime")
    if isinstance(runtime, dict) and "adapter" in runtime:
        runtime.pop("adapter")
        changed = True
    templates = payload.get("templates")
    if isinstance(templates, dict):
        for legacy in ("compose", "systemd"):
            if legacy in templates:
                templates.pop(legacy)
                changed = True
        if "docker" not in templates and changed:
            templates["docker"] = "config-docker/v1"
    return changed


def _restore(payload: dict) -> bool:
    changed = False
    runtime = payload.get("runtime")
    if isinstance(runtime, dict) and "adapter" not in runtime:
        runtime["adapter"] = "docker-compose"
        changed = True
    templates = payload.get("templates")
    if isinstance(templates, dict):
        if "compose" not in templates:
            templates["compose"] = "config-compose/v1"
            changed = True
        if templates.pop("docker", None) is not None:
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
