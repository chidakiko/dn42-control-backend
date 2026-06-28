from __future__ import annotations

"""observability 纯计算层单测:流量差分 / fleet 桶对齐 / 链路·BGP 状态判定。"""

from app.services.observability import (
    aggregate_fleet_traffic,
    bgp_health,
    compute_node_traffic,
    node_bgp_sessions,
    node_links,
    traffic_series_from_samples,
    wg_status,
)


def _snap(ts: str, rx: int, tx: int) -> dict:
    return {
        "created_at": ts,
        "payload": {
            "captured_at": ts,
            "wireguard_interfaces": [
                {"name": "wg1", "peers": [{"transfer_rx_bytes": rx, "transfer_tx_bytes": tx}]}
            ],
        },
    }


def test_compute_node_traffic_rates() -> None:
    events = [
        _snap("2026-06-27T01:05:00+00:00", 31_000_000, 9_500_000),  # 乱序(新在前)
        _snap("2026-06-27T01:00:00+00:00", 1_000_000, 500_000),
    ]
    pts = compute_node_traffic(events)
    assert len(pts) == 1
    assert pts[0]["captured_at"] == "2026-06-27T01:05:00+00:00"
    assert pts[0]["rx_bytes_per_sec"] == 30_000_000 / 300  # 100000
    assert pts[0]["tx_bytes_per_sec"] == 9_000_000 / 300


def test_compute_node_traffic_clamps_counter_reset() -> None:
    # 第二份计数比第一份小(接口重建归零)-> 速率钳到 0,不画负尖峰。
    events = [
        _snap("2026-06-27T01:00:00+00:00", 50_000_000, 50_000_000),
        _snap("2026-06-27T01:05:00+00:00", 1_000, 1_000),
    ]
    pts = compute_node_traffic(events)
    assert pts[0]["rx_bytes_per_sec"] == 0
    assert pts[0]["tx_bytes_per_sec"] == 0


def test_aggregate_fleet_traffic_sums_per_bucket() -> None:
    a = [{"captured_at": "2026-06-27T01:05:00+00:00", "rx_bytes_per_sec": 100.0, "tx_bytes_per_sec": 10.0}]
    b = [{"captured_at": "2026-06-27T01:05:10+00:00", "rx_bytes_per_sec": 200.0, "tx_bytes_per_sec": 20.0}]
    out = aggregate_fleet_traffic([a, b], bucket_s=300)
    # 两个点都落进同一个 5min 桶 -> 求和。
    assert len(out) == 1
    assert out[0]["rx_bytes_per_sec"] == 300.0
    assert out[0]["tx_bytes_per_sec"] == 30.0


def test_traffic_series_from_samples_rates() -> None:
    # 30s 轻量采样(累计计数)差分出速率;乱序入参内部按时间排序。
    samples = [
        {"captured_at": "2026-06-27T01:00:30+00:00", "rx_bytes": 4_000, "tx_bytes": 2_000},
        {"captured_at": "2026-06-27T01:00:00+00:00", "rx_bytes": 1_000, "tx_bytes": 500},
        {"captured_at": "2026-06-27T01:01:00+00:00", "rx_bytes": 7_000, "tx_bytes": 3_500},
    ]
    pts = traffic_series_from_samples(samples)
    assert len(pts) == 2
    assert pts[0]["captured_at"] == "2026-06-27T01:00:30+00:00"
    assert pts[0]["rx_bytes_per_sec"] == 3_000 / 30  # (4000-1000)/30
    assert pts[1]["rx_bytes_per_sec"] == 3_000 / 30  # (7000-4000)/30


def test_traffic_series_from_samples_clamps_reset() -> None:
    # 计数器重置(后值更小)钳到 0,不画负尖峰;空/单点产出空列表。
    samples = [
        {"captured_at": "2026-06-27T01:00:00+00:00", "rx_bytes": 9_000, "tx_bytes": 9_000},
        {"captured_at": "2026-06-27T01:00:30+00:00", "rx_bytes": 100, "tx_bytes": 100},
    ]
    pts = traffic_series_from_samples(samples)
    assert pts[0]["rx_bytes_per_sec"] == 0
    assert traffic_series_from_samples([]) == []
    assert traffic_series_from_samples([samples[0]]) == []


def test_wg_status_thresholds() -> None:
    assert wg_status(10) == "up"
    assert wg_status(180) == "up"
    assert wg_status(300) == "stale"
    assert wg_status(600) == "stale"
    assert wg_status(700) == "down"
    assert wg_status(None) == "down"  # 从未握手


def test_node_links_extracts_per_peer() -> None:
    snap = {
        "wireguard_interfaces": [
            {
                "name": "wg-hkg1",
                "peers": [
                    {
                        "public_key": "PUB=",
                        "endpoint": "1.2.3.4:51820",
                        "last_handshake_seconds": 12,
                        "transfer_rx_bytes": 100,
                        "transfer_tx_bytes": 200,
                    }
                ],
            }
        ]
    }
    links = node_links(snap)
    assert len(links) == 1
    assert links[0]["interface"] == "wg-hkg1"
    assert links[0]["type"] == "wireguard"
    assert links[0]["status"] == "up"
    assert links[0]["endpoint"] == "1.2.3.4:51820"
    assert node_links(None) == []


def test_bgp_health_mapping() -> None:
    assert bgp_health("Established") == "up"
    assert bgp_health("Active") == "connecting"
    assert bgp_health("Connect") == "connecting"
    assert bgp_health("Idle") == "down"
    assert bgp_health("") == "down"


def test_node_bgp_sessions_scope_by_config() -> None:
    snap = {
        "bgp_protocols": [
            {"name": "ibgp_pvg2", "session": "ibgp_pvg2", "state": "Established"},
            {"name": "cow_4242423999", "session": "cow_4242423999", "state": "Established"},
            {"name": "iedon_mp", "session": "iedon_mp", "state": "Idle"},
        ]
    }
    configured = {"cow_4242423999", "iedon_mp"}  # 这俩有 eBGP 配置 spec
    sessions = node_bgp_sessions(snap, configured)
    by_name = {s["name"]: s for s in sessions}
    assert by_name["ibgp_pvg2"]["scope"] == "internal"  # 无配置 = iBGP 合成
    assert by_name["cow_4242423999"]["scope"] == "external"
    assert by_name["iedon_mp"]["scope"] == "external"
    assert by_name["iedon_mp"]["health"] == "down"  # Idle
    assert by_name["cow_4242423999"]["health"] == "up"
