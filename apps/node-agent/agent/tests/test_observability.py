from __future__ import annotations

"""agent 自观测原语：CPU 采样、慢周期告警、RSS 读取。"""

import logging

from agent.observability import CpuSampler, current_rss_mb, warn_if_slow


def _sampler(walls: list[float], cpus: list[float]) -> CpuSampler:
    """注入脚本化时钟：__init__ 读首值，sample() 读次值。"""

    wi = iter(walls)
    ci = iter(cpus)
    return CpuSampler(wall_clock=lambda: next(wi), cpu_clock=lambda: next(ci))


def test_cpu_sampler_computes_percent_over_window() -> None:
    # Δcpu=6 / Δwall=10 → 60%
    sampler = _sampler([100.0, 110.0], [5.0, 11.0])
    percent, total = sampler.sample()
    assert percent == 60.0
    assert total == 11.0


def test_cpu_sampler_can_exceed_100_on_multicore() -> None:
    # Δcpu=1.5 / Δwall=1.0 → 150%（多核：一个采样窗口里用了 1.5 核·秒）
    sampler = _sampler([0.0, 1.0], [0.0, 1.5])
    percent, _ = sampler.sample()
    assert percent == 150.0


def test_cpu_sampler_zero_interval_returns_zero() -> None:
    # 墙钟没动（区间过短）→ 不臆造比例，返回 0。
    sampler = _sampler([5.0, 5.0], [1.0, 9.0])
    percent, _ = sampler.sample()
    assert percent == 0.0


def test_warn_if_slow_triggers_above_threshold(caplog) -> None:
    with caplog.at_level(logging.WARNING):
        assert warn_if_slow("routing-collect", 600.0, 300.0) is True
    assert "性能回归" in caplog.text
    # 未超阈值 / 阈值<=0（关闭）→ 不告警
    assert warn_if_slow("routing-collect", 1.0, 300.0) is False
    assert warn_if_slow("routing-collect", 999.0, 0.0) is False


def test_current_rss_mb_best_effort() -> None:
    # Linux 返回正数 MB；非 Linux / 读不到返回 None——两者都合法，不臆造。
    value = current_rss_mb()
    assert value is None or (isinstance(value, float) and value > 0)
