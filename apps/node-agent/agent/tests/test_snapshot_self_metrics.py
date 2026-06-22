from __future__ import annotations

"""RuntimeSnapshot 携带 agent 自观测（self_metrics）：映射、序列化、旧 agent 兼容。"""

import json

from dn42_schemas.testing import build_hkg1_example_state

from agent.collectors.snapshot import build_runtime_snapshot
from agent.metrics import ReconcileMetrics


def test_snapshot_carries_self_metrics_from_reconcile_metrics() -> None:
    metrics = ReconcileMetrics(
        total_reconciles=12,
        total_failures=1,
        consecutive_failures=0,
        last_duration_seconds=1.4,
        cpu_percent=42.6,
        rss_mb=190.0,
        last_routing_collect_seconds=1.45,
        last_reresolve_seconds=0.3,
        self_observed_at="2026-06-22T20:00:00Z",
    )
    snapshot = build_runtime_snapshot(build_hkg1_example_state(), metrics=metrics)
    sm = snapshot.self_metrics
    assert sm is not None
    assert sm.cpu_percent == 42.6
    assert sm.rss_mb == 190.0
    assert sm.last_routing_collect_seconds == 1.45
    assert sm.last_reconcile_duration_seconds == 1.4  # 映射自 last_duration_seconds
    assert sm.total_reconciles == 12
    assert sm.consecutive_failures == 0


def test_self_metrics_rides_in_serialized_snapshot() -> None:
    # 控制面靠 model_dump_json 落 last_snapshot——self_metrics 必须在序列化产物里。
    metrics = ReconcileMetrics(cpu_percent=80.0, rss_mb=128.0)
    snapshot = build_runtime_snapshot(build_hkg1_example_state(), metrics=metrics)
    blob = json.loads(snapshot.model_dump_json())
    assert blob["self_metrics"]["cpu_percent"] == 80.0
    assert blob["self_metrics"]["rss_mb"] == 128.0


def test_snapshot_without_metrics_omits_self_metrics() -> None:
    # 不传 metrics（旧 agent / 桩）→ None，控制面与前端据此降级，不报错。
    snapshot = build_runtime_snapshot(build_hkg1_example_state())
    assert snapshot.self_metrics is None
