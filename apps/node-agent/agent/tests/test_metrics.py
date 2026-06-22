from __future__ import annotations

"""reconcile 指标累计与持久化的单元测试。"""

from pathlib import Path

from agent.metrics import (
    ReconcileMetrics,
    load_metrics,
    record_reconcile,
    record_self_observation,
)


def test_load_missing_returns_zero(tmp_path: Path) -> None:
    metrics = load_metrics(tmp_path / "metrics.json")
    assert metrics == ReconcileMetrics()


def test_self_observation_updates_without_clobbering_reconcile(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    record_reconcile(path, status="succeeded", duration_seconds=1.0, generation=5)
    metrics = record_self_observation(
        path, cpu_percent=42.0, rss_mb=128.0, routing_collect_seconds=600.0
    )
    # reconcile 字段原样保留
    assert metrics.total_reconciles == 1
    assert metrics.last_status == "succeeded"
    assert metrics.last_generation == 5
    # 自观测字段写入 + 持久化
    assert metrics.cpu_percent == 42.0
    assert metrics.rss_mb == 128.0
    assert metrics.last_routing_collect_seconds == 600.0
    assert metrics.self_observed_at is not None
    assert load_metrics(path) == metrics


def test_self_observation_partial_fields_preserve_others(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    record_self_observation(path, cpu_percent=10.0)
    metrics = record_self_observation(path, reresolve_seconds=2.5)
    # 只传 reresolve 不该清掉上次写的 cpu_percent
    assert metrics.cpu_percent == 10.0
    assert metrics.last_reresolve_seconds == 2.5


def test_reconcile_preserves_self_observation_fields(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    record_self_observation(path, cpu_percent=33.0)
    metrics = record_reconcile(path, status="succeeded", duration_seconds=1.0, generation=1)
    # reconcile 写入不该清掉自观测字段（各动各的）
    assert metrics.cpu_percent == 33.0
    assert metrics.total_reconciles == 1


def test_record_success_accumulates(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    record_reconcile(path, status="succeeded", duration_seconds=1.234, generation=5)
    metrics = record_reconcile(path, status="succeeded", duration_seconds=2.0, generation=6)

    assert metrics.total_reconciles == 2
    assert metrics.total_failures == 0
    assert metrics.consecutive_failures == 0
    assert metrics.last_status == "succeeded"
    assert metrics.last_duration_seconds == 2.0
    assert metrics.last_generation == 6
    # 持久化后重新加载一致。
    assert load_metrics(path) == metrics


def test_failures_increment_and_reset(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    record_reconcile(path, status="failed", duration_seconds=1.0, generation=1)
    record_reconcile(path, status="failed", duration_seconds=1.0, generation=1)
    metrics = load_metrics(path)
    assert metrics.total_failures == 2
    assert metrics.consecutive_failures == 2

    # 一次成功复位连续失败，但累计失败保留。
    metrics = record_reconcile(path, status="succeeded", duration_seconds=1.0, generation=2)
    assert metrics.total_failures == 2
    assert metrics.consecutive_failures == 0


def test_skipped_does_not_count_as_failure(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    metrics = record_reconcile(path, status="skipped", duration_seconds=0.1, generation=None)
    assert metrics.total_failures == 0
    assert metrics.consecutive_failures == 0
    assert metrics.last_status == "skipped"


def test_corrupt_file_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "metrics.json"
    path.write_text("{not json", encoding="utf-8")
    # 损坏文件按零值起算，不抛。
    metrics = record_reconcile(path, status="succeeded", duration_seconds=1.0, generation=1)
    assert metrics.total_reconciles == 1
