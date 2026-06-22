from __future__ import annotations

"""Agent 自观测原语：进程 CPU% 采样、RSS 读取、慢周期告警。

目标：把"agent 自身忙不忙、哪个背景循环变慢了"做进常驻进程，写进 metrics.json，
经 ``doctor`` / 外部探针即可一眼看到——**不必再临时往生产装 py-spy**。

设计与既有观测面一致：纯函数 + 注入式时钟，单测不碰真实时间/系统；产出的数值由
``watch`` 的背景循环周期采集后落到节点 metrics 文件，与 reconcile 指标同源。
"""

import logging
import os
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class CpuSampler:
    """进程级 CPU 占用采样器（**含全部线程**）。

    用 ``os.times()``（跨平台）累计本进程 user+system CPU 秒，配合墙钟算两次
    ``sample()`` 之间的 CPU%。多核可超 100%。关键点：``os.times()`` 是进程级、
    囊括所有线程——所以即便 CPU 烧在 ``run_in_executor`` 的工作线程里（如曾经的
    RPKI 热循环），也照样被捕获。
    """

    def __init__(
        self,
        *,
        wall_clock: Callable[[], float] = time.monotonic,
        cpu_clock: Callable[[], float] | None = None,
    ) -> None:
        self._wall_clock = wall_clock
        self._cpu_clock = cpu_clock or _process_cpu_seconds
        self._last_wall = self._wall_clock()
        self._last_cpu = self._cpu_clock()

    def sample(self) -> tuple[float, float]:
        """返回 ``(cpu_percent, cpu_seconds_total)``。

        ``cpu_percent`` 是距上次采样这段窗口的平均 CPU 占用（多核可 >100）；区间过短
        （<1ms）无意义时返回 0。``cpu_seconds_total`` 是进程自启动累计 CPU 秒。
        """

        now_wall = self._wall_clock()
        now_cpu = self._cpu_clock()
        delta_wall = now_wall - self._last_wall
        delta_cpu = now_cpu - self._last_cpu
        self._last_wall = now_wall
        self._last_cpu = now_cpu
        percent = round(delta_cpu / delta_wall * 100, 1) if delta_wall > 1e-3 else 0.0
        return (max(percent, 0.0), round(now_cpu, 3))


def _process_cpu_seconds() -> float:
    times = os.times()
    return times.user + times.system  # 进程自身 user+sys（含全部线程）


def current_rss_mb() -> float | None:
    """本进程常驻内存（MB），best-effort。

    Linux 读 ``/proc/self/status`` 的 ``VmRSS``；非 Linux / 读不到时返回 ``None``
    （观测缺失，不臆造）。
    """

    try:
        with open("/proc/self/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)  # kB -> MB
    except (OSError, ValueError, IndexError):
        return None
    return None


def warn_if_slow(kind: str, duration_seconds: float, threshold_seconds: float) -> bool:
    """背景循环单轮耗时超阈值则 WARN，返回是否告警。

    阈值通常取该循环自身的间隔：单轮耗时 > 间隔意味着"追不上节奏"（采集永远跑不完、
    下一轮接着排队），正是 RPKI O(路由×ROA) 爆炸那类性能回归的特征。把它做成自动告警，
    下次同类回归无需有人盯 CPU 就能从日志发现。
    """

    if threshold_seconds > 0 and duration_seconds > threshold_seconds:
        logger.warning(
            "observability: %s 单轮耗时 %.1fs 超过阈值 %.1fs——疑似性能回归"
            "（该循环追不上节奏），建议排查其热点",
            kind,
            duration_seconds,
            threshold_seconds,
        )
        return True
    return False


__all__ = ["CpuSampler", "current_rss_mb", "warn_if_slow"]
