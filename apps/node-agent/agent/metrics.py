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
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from dn42_common import atomic_write_json

from .core.clock import utc_now_iso

# reconcile 与背景循环（路由/reresolve/self-monitor）从不同协程写同一文件。虽然
# 它们都在事件循环里串行执行、record_* 内无 await 不会真正交错，这把锁是防御性兜底，
# 也容纳未来从非事件循环线程调用的可能。
_WRITE_LOCK = threading.Lock()


@dataclass(slots=True)
class ReconcileMetrics:
    """agent 自观测的累计 reconcile 指标 + 背景循环 / 进程自身观测。"""

    total_reconciles: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    last_status: str | None = None
    last_duration_seconds: float | None = None
    last_generation: int | None = None
    last_reconcile_at: str | None = None
    # 背景循环耗时 + 进程自观测（self-monitor 周期写入）。免得排障时再临时装 py-spy：
    # 一眼看到哪个旁路循环变慢了、agent 自身 CPU/RSS 高不高。
    last_routing_collect_seconds: float | None = None
    last_reresolve_seconds: float | None = None
    cpu_percent: float | None = None
    rss_mb: float | None = None
    self_observed_at: str | None = None


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

    with _WRITE_LOCK:
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


def record_self_observation(
    path: Path,
    *,
    cpu_percent: float | None = None,
    rss_mb: float | None = None,
    routing_collect_seconds: float | None = None,
    reresolve_seconds: float | None = None,
    at: str | None = None,
) -> ReconcileMetrics:
    """登记一次背景循环耗时 / 进程自观测，**只更新传入的字段**，原子写盘。

    与 ``record_reconcile`` 写同一文件、同锁串行，互不覆盖对方字段（各自只动自己的）。
    任一字段缺省（``None``）即本次不更新它，便于不同循环各报各的。
    """

    with _WRITE_LOCK:
        metrics = load_metrics(path)
        if cpu_percent is not None:
            metrics.cpu_percent = cpu_percent
        if rss_mb is not None:
            metrics.rss_mb = rss_mb
        if routing_collect_seconds is not None:
            metrics.last_routing_collect_seconds = round(routing_collect_seconds, 3)
        if reresolve_seconds is not None:
            metrics.last_reresolve_seconds = round(reresolve_seconds, 3)
        metrics.self_observed_at = at or utc_now_iso()

        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, asdict(metrics))
        return metrics


__all__ = ["ReconcileMetrics", "load_metrics", "record_reconcile", "record_self_observation"]
