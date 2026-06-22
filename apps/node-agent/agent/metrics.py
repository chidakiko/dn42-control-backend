from __future__ import annotations

"""Agent reconcile 运行指标的本地持久化。

常驻进程每跑完一次 reconcile 就把结果累计写入 ``<node_dir>/metrics.json``：
总次数、累计失败、连续失败、最近一次状态 / 时长 / 世代 / 时间。这份文件是
``doctor`` 子命令与外部探针读取 agent 自身健康的唯一数据源——无需额外开端口，
排障时一眼看到"上次收敛是否成功、是否在连续失败"。

写入走 ``atomic_write_json`` 原子替换，崩溃不会留下半截文件；读取对损坏 /
缺字段的文件容错（返回零值或忽略未知键），指标文件永不阻断 reconcile。
"""

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from dn42_common import atomic_write_json

from .core.clock import utc_now_iso


@dataclass(slots=True)
class ReconcileMetrics:
    """agent 自观测的累计 reconcile 指标。"""

    total_reconciles: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    last_status: str | None = None
    last_duration_seconds: float | None = None
    last_generation: int | None = None
    last_reconcile_at: str | None = None


def load_metrics(path: Path) -> ReconcileMetrics:
    """从磁盘加载指标；文件不存在 / 损坏时返回零值。"""

    if not path.exists():
        return ReconcileMetrics()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ReconcileMetrics()
    if not isinstance(payload, dict):
        return ReconcileMetrics()
    known = {field.name for field in fields(ReconcileMetrics)}
    return ReconcileMetrics(**{key: value for key, value in payload.items() if key in known})


def record_reconcile(
    path: Path,
    *,
    status: str,
    duration_seconds: float,
    generation: int | None,
    at: str | None = None,
) -> ReconcileMetrics:
    """累计登记一次 reconcile 结果并原子写盘，返回更新后的指标。

    仅 ``status == "failed"`` 计入失败并累加连续失败计数；``succeeded`` /
    ``skipped`` 复位连续失败。``at`` 缺省取当前 UTC ISO 时间。
    """

    metrics = load_metrics(path)
    metrics.total_reconciles += 1
    if status == "failed":
        metrics.total_failures += 1
        metrics.consecutive_failures += 1
    else:
        metrics.consecutive_failures = 0
    metrics.last_status = status
    metrics.last_duration_seconds = round(duration_seconds, 3)
    metrics.last_generation = generation
    metrics.last_reconcile_at = at or utc_now_iso()

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, asdict(metrics))
    return metrics


__all__ = ["ReconcileMetrics", "load_metrics", "record_reconcile"]
