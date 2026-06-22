"""strip legacy compat residue so runtime shims can be removed

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-19 00:00:00.000000

清掉为兼容前代而留在 DB 里的残留，使运行时兼容垫片可以删除（与 f6a7b8c9d0e1 /
a7b8c9d0e1f2 同套路：先在数据层剥离，再删 schema/服务里的垫片）：

- ``nodes.base_template`` 与 ``generations.snapshot`` JSON：
  - 删 ``lookglass`` 字段（looking glass 已彻底移除）；
  - 删 ``runtime.services`` 里 ``looking-glass-*`` 角色的服务；
  - 把 ``runtime.services[].ports`` 里遗留的字符串端口（compose 风格
    ``"host:container/proto"``）归一化成结构化 dict（去 compose 后 schema 仅接受 dict）。
- ``node_routing.aggregates`` JSON：删 prefilter 里遗留的 ``unknown`` 键
  （节点级 + 每对端）；「未知」RPKI 态已从协议移除。

downgrade 不可逆（被删的历史值无意义、且新 schema 也不再接受），留作 no-op。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LOOKGLASS_ROLES = frozenset({"looking-glass-proxy", "looking-glass-frontend"})
_DESIRED_STATE_TARGETS = (
    ("nodes", "node_id", "base_template"),
    ("generations", "id", "snapshot"),
)


def _parse_port_range(value: str) -> tuple[int, int | None]:
    if "-" not in value:
        return int(value), None
    start, end = value.split("-", 1)
    return int(start), int(end)


def _port_from_string(value: str) -> dict:
    """compose 风格端口字符串 -> 结构化 dict（与历史 _port_publish_spec_from_string 等价）。"""

    protocol = "tcp"
    body = value.strip()
    if "/" in body:
        body, protocol = body.rsplit("/", 1)
    parts = body.split(":")
    if len(parts) == 1:
        start, end = _parse_port_range(parts[0])
        return {"container_port": start, "container_port_end": end, "protocol": protocol}
    if len(parts) == 2:
        host_start, host_end = _parse_port_range(parts[0])
        container_start, container_end = _parse_port_range(parts[1])
        return {
            "host_port": host_start,
            "host_port_end": host_end,
            "container_port": container_start,
            "container_port_end": container_end,
            "protocol": protocol,
        }
    host_start, host_end = _parse_port_range(parts[1])
    container_start, container_end = _parse_port_range(parts[2])
    return {
        "host_ip": parts[0],
        "host_port": host_start,
        "host_port_end": host_end,
        "container_port": container_start,
        "container_port_end": container_end,
        "protocol": protocol,
    }


def _strip_desired_state(payload: dict) -> bool:
    changed = False
    # 键存在即剥（值可能是 null）：StrictModel extra="forbid" 连 ``"lookglass": null``
    # 这种空值残留也会拒绝，故不能只在值非 None 时才剥。
    if "lookglass" in payload:
        payload.pop("lookglass", None)
        changed = True
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        return changed
    services = runtime.get("services")
    if not isinstance(services, list):
        return changed
    kept = [
        svc
        for svc in services
        if not (isinstance(svc, dict) and svc.get("role") in _LOOKGLASS_ROLES)
    ]
    if len(kept) != len(services):
        runtime["services"] = kept
        changed = True
    for svc in kept:
        if not isinstance(svc, dict):
            continue
        ports = svc.get("ports")
        if not isinstance(ports, list) or not any(isinstance(p, str) for p in ports):
            continue
        svc["ports"] = [_port_from_string(p) if isinstance(p, str) else p for p in ports]
        changed = True
    return changed


def _rewrite_desired_state() -> None:
    connection = op.get_bind()
    for table, pk, column in _DESIRED_STATE_TARGETS:
        rows = connection.execute(
            sa.text(f"SELECT {pk}, {column} FROM {table}")  # noqa: S608 - 常量表名
        ).fetchall()
        for key, raw in rows:
            payload = raw if isinstance(raw, dict) else json.loads(raw) if raw else None
            if not isinstance(payload, dict) or not _strip_desired_state(payload):
                continue
            connection.execute(
                sa.text(f"UPDATE {table} SET {column} = :payload WHERE {pk} = :key"),  # noqa: S608
                {"payload": json.dumps(payload, ensure_ascii=False), "key": key},
            )


def _strip_routing_unknown() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT node_id, aggregates FROM node_routing")).fetchall()
    for node_id, raw in rows:
        payload = raw if isinstance(raw, dict) else json.loads(raw) if raw else None
        if not isinstance(payload, dict):
            continue
        prefilter = payload.get("prefilter")
        if not isinstance(prefilter, dict):
            continue
        changed = prefilter.pop("unknown", None) is not None
        peers = prefilter.get("peers")
        if isinstance(peers, list):
            for peer in peers:
                if isinstance(peer, dict) and peer.pop("unknown", None) is not None:
                    changed = True
        if changed:
            connection.execute(
                sa.text("UPDATE node_routing SET aggregates = :payload WHERE node_id = :key"),
                {"payload": json.dumps(payload, ensure_ascii=False), "key": node_id},
            )


def upgrade() -> None:
    _rewrite_desired_state()
    _strip_routing_unknown()


def downgrade() -> None:
    # 不可逆：剥掉的 lookglass / looking-glass 服务 / 字符串端口 / prefilter unknown 都是
    # 已废弃的前代残留，新 schema 也不再接受，故不还原。
    pass
