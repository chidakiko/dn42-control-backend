from __future__ import annotations

"""WebUI 专用观测聚合的**纯计算**层。

把"从一堆通用上报里拼视图"的活从浏览器挪到服务端:流量时间线(快照里 WG 累计
收发字节的差分速率)、链路状态、BGP 会话状态(内外 + Established 判定)。全部是无副作用
的纯函数,吃 ``NodeStatusStore`` 给的原始 dict,吐前端可直接渲染的结构,便于单测。
"""

from datetime import datetime


# ---- 时间戳 ----

def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _event_ts(event: dict) -> str | None:
    """优先用快照自带的 captured_at,回退到入库时间。"""

    payload = event.get("payload") or {}
    return payload.get("captured_at") or event.get("created_at")


# ---- WG 流量 ----

def _wg_totals(payload: dict) -> tuple[int, int]:
    """一份快照里全部 WG peer 的累计收 / 发字节之和。"""

    rx = tx = 0
    for iface in payload.get("wireguard_interfaces") or []:
        for peer in iface.get("peers") or []:
            rx += peer.get("transfer_rx_bytes") or 0
            tx += peer.get("transfer_tx_bytes") or 0
    return rx, tx


def _diff_rates(points: list[tuple[str | None, int, int]]) -> list[dict]:
    """把按时间升序的累计计数点 ``(ts, rx, tx)`` 差分成逐区间速率(字节/秒)。

    相邻两点的 ``Δ字节 / Δ秒`` 即该区间平均速率。计数器在接口重建时会归零,故 ``Δ``
    钳到 ≥0,避免画成负尖峰;``Δ秒 ≤ 0`` 的相邻点(时钟回拨 / 同刻重复采样)跳过。
    """

    out: list[dict] = []
    for (ts0, rx0, tx0), (ts1, rx1, tx1) in zip(points, points[1:]):
        a, b = _parse(ts0), _parse(ts1)
        if a is None or b is None:
            continue
        dt = (b - a).total_seconds()
        if dt <= 0:
            continue
        out.append(
            {
                "captured_at": ts1,
                "rx_bytes_per_sec": max(0, rx1 - rx0) / dt,
                "tx_bytes_per_sec": max(0, tx1 - tx0) / dt,
            }
        )
    return out


def compute_node_traffic(events: list[dict]) -> list[dict]:
    """从一节点的 snapshot 事件历史算逐区间吞吐率(字节/秒)。

    每份快照是累计计数,相邻两份的 ``Δ字节 / Δ秒`` 即该区间平均速率。事件按时间升序
    处理。这是没有 30s 轻量采样(Redis 热窗口)时的回退路径——分辨率即快照节奏(~5min)。
    """

    points = sorted(
        (
            (_event_ts(e), *_wg_totals(e.get("payload") or {}))
            for e in events
        ),
        key=lambda t: t[0] or "",
    )
    return _diff_rates(points)


def traffic_series_from_samples(samples: list[dict]) -> list[dict]:
    """从轻量 WG 流量采样(``{captured_at, rx_bytes, tx_bytes}``)差分出吞吐时间线。

    采样是 agent 30s 轻量循环上报的全 peer 累计收 / 发字节之和(``WireGuardTrafficSample``)。
    入参不要求有序,内部按 ``captured_at`` 升序后逐区间差分,产出与 ``compute_node_traffic``
    同结构的速率点——只是分辨率更高(~30s)。空 / 单点采样产出空列表。
    """

    points = sorted(
        ((s.get("captured_at"), s.get("rx_bytes") or 0, s.get("tx_bytes") or 0) for s in samples),
        key=lambda t: t[0] or "",
    )
    return _diff_rates(points)


def _bucket(ts: str | None, bucket_s: int) -> int | None:
    d = _parse(ts)
    if d is None:
        return None
    return int(d.timestamp()) // bucket_s * bucket_s


def aggregate_fleet_traffic(per_node: list[list[dict]], *, bucket_s: int = 300) -> list[dict]:
    """把多节点的逐区间速率按固定时间桶对齐求和,得到 fleet 级吞吐时间线。

    各节点快照时刻不一致,按 ``bucket_s``(默认 5 分钟,贴合快照节奏)向下取整对齐;
    同节点同桶取最后一个值(防一桶多采重复计),再跨节点求和。
    """

    buckets: dict[int, list[float]] = {}
    for node_points in per_node:
        per_bucket: dict[int, tuple[float, float]] = {}
        for p in node_points:
            b = _bucket(p["captured_at"], bucket_s)
            if b is not None:
                per_bucket[b] = (p["rx_bytes_per_sec"], p["tx_bytes_per_sec"])
        for b, (rx, tx) in per_bucket.items():
            agg = buckets.setdefault(b, [0.0, 0.0])
            agg[0] += rx
            agg[1] += tx
    return [
        {
            "captured_at": datetime.fromtimestamp(b).astimezone().isoformat(),
            "rx_bytes_per_sec": v[0],
            "tx_bytes_per_sec": v[1],
        }
        for b, v in sorted(buckets.items())
    ]


# ---- 链路状态 ----

def wg_status(age_seconds: int | None) -> str:
    """WG 隧道存活判定:握手新鲜=up,渐旧=stale,久未/从未=down。"""

    if age_seconds is None:
        return "down"
    if age_seconds <= 180:
        return "up"
    if age_seconds <= 600:
        return "stale"
    return "down"


def node_links(snapshot: dict | None) -> list[dict]:
    """从 last_snapshot 提取各 WG 链路 per-peer 状态(类型化,含 up/stale/down)。"""

    out: list[dict] = []
    for iface in (snapshot or {}).get("wireguard_interfaces") or []:
        for peer in iface.get("peers") or []:
            age = peer.get("last_handshake_seconds")
            out.append(
                {
                    "interface": iface.get("name"),
                    "type": "wireguard",
                    "public_key": peer.get("public_key"),
                    "endpoint": peer.get("endpoint"),
                    "last_handshake_seconds": age,
                    "transfer_rx_bytes": peer.get("transfer_rx_bytes") or 0,
                    "transfer_tx_bytes": peer.get("transfer_tx_bytes") or 0,
                    "status": wg_status(age),
                }
            )
    return out


# ---- BGP 会话状态 ----

def bgp_health(state: str) -> str:
    """bird BGP state → 红绿灯:Established=up,握手中=connecting,其余=down。"""

    s = (state or "").lower()
    if s in ("established", "up"):
        return "up"
    if s in ("active", "connect", "opensent", "openconfirm"):
        return "connecting"
    return "down"


def node_bgp_sessions(snapshot: dict | None, configured_names: set[str]) -> list[dict]:
    """从 last_snapshot.bgp_protocols 提取全部 BGP 会话状态,内外按配置判定。

    ``configured_names`` 是 DesiredState 里 eBGP 会话名(及其 bird 协议名)的集合;命中
    即外部(有可编辑 spec),否则内部(iBGP,合成、无 spec)——比前端按名字前缀启发更准。
    """

    out: list[dict] = []
    for proto in (snapshot or {}).get("bgp_protocols") or []:
        name = proto.get("name")
        session = proto.get("session") or name
        external = name in configured_names or session in configured_names
        out.append(
            {
                "name": name,
                "session": session,
                "scope": "external" if external else "internal",
                "state": proto.get("state"),
                "health": bgp_health(proto.get("state") or ""),
                "since": proto.get("since"),
                "info": proto.get("info"),
            }
        )
    return out


__all__ = [
    "compute_node_traffic",
    "traffic_series_from_samples",
    "aggregate_fleet_traffic",
    "wg_status",
    "node_links",
    "bgp_health",
    "node_bgp_sessions",
]
