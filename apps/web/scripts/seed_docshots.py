#!/usr/bin/env python3
"""Feed the seeded control-server with realistic demo data for doc screenshots.

`docs/guides/web-ui.md` 的配图需要"看起来像真在跑"的数据：路由分析、健康趋势、
机群路由概览、DNS 组等都依赖 agent 上报的观测，而光靠 `seed_bootstrap_node` 只
建出静态配置（节点 + 接口 + BGP 会话），路由 / 趋势 / DNS 这些新功能页全是空态。

本脚本在控制面起好后跑一次，通过 HTTP 注入：
  - 多轮 reconcile report + apply-result（drift 递减到 0）→ 节点判 ok、overview
    顶部出现趋势小卡（NodeTrends）。
  - 多张路由全表快照（带 RPKI 分布 + 过滤前 per-peer 统计 + 无效/被拒明细）→
    节点「路由表」页 + 仪表盘「机群路由」全活。
  - 一个 DNS 组（anycast bind 地址）+ 权威 zone + 扁平记录，并把 edge1 订阅进去
    → 「DNS 组」页 + 节点「DNS」页有内容。
  - 一个 enrollment token + 一条 pending 注册 → 「注册令牌」「注册审批」页有内容。

只用标准库（urllib），不依赖 requests/httpx。所有写操作都对应 Web UI 里真实的
管理动作，注入的数据与生产形态一致。

用法（端口/令牌须与 doc-shots.mjs 顶部一致）：
    DN42_DOCSHOTS_API=http://127.0.0.1:8001 \
    DN42_CONTROL_ADMIN_TOKEN=dev-admin-token \
    python apps/web/scripts/seed_docshots.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib import error, request

API = os.environ.get("DN42_DOCSHOTS_API", "http://127.0.0.1:8001")
ADMIN_TOKEN = os.environ.get("DN42_CONTROL_ADMIN_TOKEN", "dev-admin-token")
ENROLL_TOKEN = os.environ.get("DN42_CONTROL_ENROLLMENT_TOKEN", "enroll-token")
NODE = os.environ.get("DN42_DOCSHOTS_NODE", "edge1")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(minutes_ago: float = 0) -> str:
    return (_now() - timedelta(minutes=minutes_ago)).isoformat()


def _call(method: str, path: str, body: object | None = None, token: str | None = None) -> object:
    data = None if body is None else json.dumps(body).encode()
    req = request.Request(f"{API}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else None
    except error.HTTPError as exc:
        detail = exc.read().decode()
        raise SystemExit(f"FAIL {method} {path} -> HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise SystemExit(f"FAIL {method} {path} -> {exc}. Is the control-server up at {API}?") from exc


def admin(method: str, path: str, body: object | None = None) -> object:
    return _call(method, path, body, token=ADMIN_TOKEN)


# --------------------------------------------------------------------------- #
# 1) Provision the demo node (production lifespan no longer auto-seeds) and pin
#    a fixed agent token so the agent endpoints below authenticate.
# --------------------------------------------------------------------------- #
AGENT_TOKEN = os.environ.get("DN42_CONTROL_BOOTSTRAP_AGENT_TOKEN", "mvp-agent-token")


def provision_node() -> str:
    nodes = admin("GET", "/api/v1/admin/nodes")
    assert isinstance(nodes, list)
    if any(n.get("node_id") == NODE for n in nodes):
        print(f"  {NODE} already provisioned")
        return AGENT_TOKEN

    # Build the canonical HKG1 example state, patch demo WG keys (mirrors
    # apps/control-server/app/db/seed.py), and provision it via the admin API.
    from dn42_schemas.testing import build_hkg1_example_state

    state = build_hkg1_example_state()
    demo_keys = {
        "as4242420001": {
            "private": "s2ljcc2rBbcmSbpSlQO3xZK20RqSxPFOcYM39Ge678M=",
            "public": "ZBzpHBTCXDYmjXzsiyZ+eWYClAQX10pCgr+Lr+oJlbc=",
        },
        "igp-edge2": {
            "private": "NGC5eyocjJudNHf9EoaCCIRH50NZtaMHfyZT1CqpPAs=",
            "public": "dqU1WhGGCztFvUaSvV3PDNPRXtjm87spbQcQITzlaj0=",
        },
    }
    patched = []
    for iface in state.interfaces:
        keys = demo_keys.get(iface.name)
        if not keys:
            patched.append(iface)
            continue
        peer = iface.wireguard_peer
        patched.append(
            iface.model_copy(
                update={
                    "private_key_ref": keys["private"],
                    "wireguard_peer": (
                        peer.model_copy(update={"public_key": keys["public"]}) if peer else None
                    ),
                }
            )
        )
    state = state.model_copy(update={"interfaces": patched})

    admin(
        "POST",
        "/api/v1/admin/provision",
        {"state": state.model_dump(mode="json"), "agent_token": AGENT_TOKEN},
    )
    print(f"  provisioned {NODE} (generation={state.generation})")
    return AGENT_TOKEN


# --------------------------------------------------------------------------- #
# 2) Reconcile reports + apply results — drift trending down to 0 (=> ok).
# --------------------------------------------------------------------------- #
def feed_status(agent_token: str) -> None:
    # oldest -> newest so the latest stored report (0 drift) drives health=ok.
    drift_rounds = [3, 2, 2, 1, 1, 0]
    n = len(drift_rounds)
    for i, drift_count in enumerate(drift_rounds):
        minutes = (n - 1 - i) * 7  # ~7 min apart, last one is "now"
        drift = [
            {
                "component": "container",
                "name": "dn42-bird-router",
                "severity": "warning",
                "message": "config hash drifted, recreate pending",
                "desired": "running (hash a1b2c3)",
                "observed": "running (hash 9f8e7d)",
            }
            for _ in range(min(drift_count, 1))
        ]
        # pad with a second, lower-severity item when drift_count > 1
        if drift_count > 1:
            drift.append(
                {
                    "component": "interface",
                    "name": "as4242420001",
                    "severity": "info",
                    "message": "MTU differs from desired",
                    "desired": "1420",
                    "observed": "1500",
                }
            )
        status = "succeeded" if drift_count == 0 else "degraded"
        _call(
            "POST",
            "/api/v1/agent/reconciliation-report",
            {
                "node_id": NODE,
                "desired_generation": 1,
                "observed_generation": 1,
                "status": status,
                "captured_at": _ts(minutes),
                "drift": drift,
            },
            token=agent_token,
        )
        _call(
            "POST",
            "/api/v1/agent/apply-result",
            {
                "node_id": NODE,
                "generation": 1,
                "status": "succeeded",
                "started_at": _ts(minutes + 0.4),
                "finished_at": _ts(minutes),
                "plan_summary": {"create": 0, "update": 1 if drift_count else 0, "delete": 0, "noop": 12},
                "applied_files": [],
                "errors": [],
            },
            token=agent_token,
        )
    print(f"  fed {n} reconcile reports + apply results (drift {drift_rounds[0]}→0)")

    # A runtime snapshot so the overview/internal liveness has observed containers.
    _call(
        "POST",
        "/api/v1/agent/runtime-snapshot",
        {
            "node_id": NODE,
            "generation": 1,
            "captured_at": _ts(0),
            "containers": [
                {"name": c, "role": role, "status": "running", "healthy": True}
                for c, role in [
                    ("dn42-router-netns-1", "router-netns"),
                    ("dn42-wg-gateway-1", "wg-gateway"),
                    ("dn42-bird-router-1", "bird-router"),
                    ("dn42-rpki-cache-1", "rpki-cache"),
                ]
            ],
            "bgp_protocols": [
                {"name": "demopeer_4242420001_ex01_v4", "state": "Established", "info": "BGP Established"},
                {"name": "demopeer_4242420001_ex01_v6", "state": "Established", "info": "BGP Established"},
                {"name": "ibgp_edge2_v4", "state": "Established", "info": "BGP Established"},
                {"name": "ibgp_edge2_v6", "state": "Established", "info": "BGP Established"},
            ],
            "bgp_observation": "observed",
        },
        token=agent_token,
    )


# --------------------------------------------------------------------------- #
# 3) Routing-table snapshots — full-table analysis (RPKI, prefilter, timeline).
# --------------------------------------------------------------------------- #
def _routes() -> list[dict]:
    """A representative DN42 routing table: local + several eBGP/iBGP peers."""
    peer_v4 = "demopeer_4242420001_ex01_v4"
    peer_v6 = "demopeer_4242420001_ex01_v6"
    ibgp = "ibgp_edge2"
    routes: list[dict] = [
        # local origin (own prefixes) — not subject to RPKI
        {"prefix": "172.20.0.0/26", "protocol": "static", "local": True, "primary": True},
        {"prefix": "fdce:1111:2222::/48", "protocol": "static", "local": True, "primary": True},
    ]

    # external IPv4 prefixes learned over the eBGP peer
    v4 = [
        ("172.20.0.128/27", 4242420001, [4242420001], "valid"),
        ("172.20.16.0/27", 4242420001, [4242420001, 4242420001], "valid"),
        ("172.22.108.0/24", 4242420113, [4242420001, 4242420113], "valid"),
        ("172.23.0.0/24", 4242421080, [4242420001, 4242421080], "not-found"),
        ("10.127.0.0/16", 64719, [4242420001, 4242420001, 64719], "not-found"),
        ("172.20.129.0/25", 4242421588, [4242420001, 4242421588], "valid"),
        ("172.20.4.0/24", 4242420149, [4242420001, 4242420149], "valid"),
        ("172.31.190.0/24", 4242422688, [4242420001, 4242422688], "invalid"),
    ]
    for prefix, origin, path, rpki in v4:
        routes.append(
            {
                "prefix": prefix,
                "origin_asn": origin,
                "as_path": path,
                "next_hop": "172.20.0.105",
                "protocol": peer_v4,
                "primary": True,
                "rpki": rpki,
                "large_communities": ["4242420000:1:62", "4242420000:101:1"],
            }
        )

    # external IPv6 prefixes
    v6 = [
        ("fdce:1111:2222:1000::/64", 4242420001, [4242420001], "valid"),
        ("fd42:d42:d42::/48", 4242420113, [4242420001, 4242420113], "valid"),
        ("fd86:bad:11b7::/48", 4242421588, [4242420001, 4242421588], "valid"),
        ("fdcf:8538:9d70::/48", 4242420001, [4242420001, 4242420001], "not-found"),
        ("fd00:beef::/48", 4242422688, [4242420001, 4242422688], "invalid"),
    ]
    for prefix, origin, path, rpki in v6:
        routes.append(
            {
                "prefix": prefix,
                "origin_asn": origin,
                "as_path": path,
                "next_hop": "fdce:1111:2222:dead::11",
                "protocol": peer_v6,
                "primary": True,
                "rpki": rpki,
                "large_communities": ["4242420000:1:62"],
            }
        )

    # iBGP-learned internal routes (edge2 loopback + a multipath backup)
    routes.append(
        {"prefix": "172.20.0.63/32", "origin_asn": 4242420000, "as_path": [], "next_hop": "198.18.1.3",
         "protocol": ibgp + "_v4", "primary": True, "rpki": None}
    )
    routes.append(
        {"prefix": "fdce:1111:2222:9501::1/128", "origin_asn": 4242420000, "as_path": [],
         "next_hop": "fdce:1111:2222:ff01::3", "protocol": ibgp + "_v6", "primary": True, "rpki": None}
    )
    # a multipath prefix: best + backup over two peers
    routes.append(
        {"prefix": "172.20.96.0/24", "origin_asn": 4242420077, "as_path": [4242420001, 4242420077],
         "next_hop": "172.20.0.105", "protocol": peer_v4, "primary": True, "rpki": "valid"}
    )
    routes.append(
        {"prefix": "172.20.96.0/24", "origin_asn": 4242420077, "as_path": [4242420000, 4242420077],
         "next_hop": "198.18.1.3", "protocol": ibgp + "_v4", "primary": False, "rpki": "valid"}
    )
    return routes


def _prefilter() -> dict:
    """Pre-filter (import-table) RPKI distribution + per-peer breakdown."""
    return {
        "received": 412,
        "accepted": 389,
        "valid": 251,
        "invalid": 5,
        "not_found": 156,
        "peers": [
            {"protocol": "demopeer_4242420001_ex01_v4", "remote_asn": 4242420001,
             "received": 233, "accepted": 220, "valid": 140, "invalid": 3, "not_found": 90},
            {"protocol": "demopeer_4242420001_ex01_v6", "remote_asn": 4242420001,
             "received": 154, "accepted": 148, "valid": 105, "invalid": 2, "not_found": 47},
            {"protocol": "ibgp_edge2_v4", "remote_asn": 4242420000,
             "received": 25, "accepted": 21, "valid": 6, "invalid": 0, "not_found": 19},
        ],
        "invalid_routes": [
            {"prefix": "172.31.190.0/24", "origin_asn": 4242422688, "protocol": "demopeer_4242420001_ex01_v4"},
            {"prefix": "fd00:beef::/48", "origin_asn": 4242422688, "protocol": "demopeer_4242420001_ex01_v6"},
            {"prefix": "172.20.255.0/24", "origin_asn": 4242420999, "protocol": "demopeer_4242420001_ex01_v4"},
        ],
        "filtered_routes": [
            {"prefix": "1.1.1.0/24", "origin_asn": 13335, "protocol": "demopeer_4242420001_ex01_v4", "reason": "out_of_range"},
            {"prefix": "172.20.0.0/26", "origin_asn": 4242420000, "protocol": "demopeer_4242420001_ex01_v4", "reason": "self_net"},
            {"prefix": "172.20.200.0/27", "origin_asn": 4242420404, "protocol": "demopeer_4242420001_ex01_v4", "reason": "as_path_too_long"},
            {"prefix": "10.0.0.0/8", "origin_asn": 65000, "protocol": "demopeer_4242420001_ex01_v4", "reason": "out_of_range"},
            {"prefix": "fd10:dead::/48", "origin_asn": 4242429999, "protocol": "demopeer_4242420001_ex01_v6", "reason": "blocked_asn"},
        ],
    }


def feed_routing(agent_token: str) -> None:
    routes = _routes()
    # several snapshots over time so the timeline + churn charts have points.
    counts = [len(routes) - 5, len(routes) - 2, len(routes) - 3, len(routes)]
    minutes = [21, 14, 7, 0]
    for c, m in zip(counts, minutes):
        snap = routes[:c] if c < len(routes) else routes
        _call(
            "POST",
            "/api/v1/agent/routing-table",
            {
                "node_id": NODE,
                "captured_at": _ts(m),
                "observation": "observed",
                "routes": snap,
                "prefilter": _prefilter(),
                "errors": [],
            },
            token=agent_token,
        )
    print(f"  fed {len(counts)} routing snapshots ({counts[-1]} routes, RPKI + prefilter)")


# --------------------------------------------------------------------------- #
# 4) DNS group + zone + records, and subscribe the node.
# --------------------------------------------------------------------------- #
def feed_dns() -> None:
    existing = admin("GET", "/api/v1/admin/dns-groups")
    assert isinstance(existing, list)
    group = next((g for g in existing if g["name"] == "ng-anycast"), None)
    if group is None:
        group = admin(
            "POST",
            "/api/v1/admin/dns-groups",
            {
                "name": "ng-anycast",
                "bind_addresses": ["172.20.0.57", "fdce:1111:2222:56::53"],
                "cache_ttl_seconds": 300,
                "forwards": [{"zone": "dn42", "upstreams": ["172.20.0.53"]}],
                "enabled": True,
            },
        )
    assert isinstance(group, dict)
    gid = group["id"]

    zones = admin("GET", f"/api/v1/admin/dns-groups/{gid}/zones")
    assert isinstance(zones, list)
    fwd_zone = next((z for z in zones if z["zone"] == "example.dn42"), None)
    if fwd_zone is None:
        fwd_zone = admin(
            "POST",
            f"/api/v1/admin/dns-groups/{gid}/zones",
            {
                "zone": "example.dn42",
                "primary_ns": "ns1.example.dn42.",
                "admin_email": "hostmaster.example.dn42.",
                "default_ttl": 3600,
                "enabled": True,
            },
        )
    assert isinstance(fwd_zone, dict)
    zid = fwd_zone["id"]

    records = [
        {"name": "@", "type": "NS", "content": "ns1.example.dn42.", "comment": "authoritative ns"},
        {"name": "ns1", "type": "AAAA", "content": "fdce:1111:2222::1", "comment": None},
        {"name": "@", "type": "A", "content": "172.20.0.62", "comment": "apex"},
        {"name": "www", "type": "AAAA", "content": "fdce:1111:2222::62", "comment": None},
        {"name": "lg", "type": "CNAME", "content": "www.example.dn42.", "comment": "looking glass"},
        {"name": "@", "type": "TXT", "content": "v=dn42; managed-by=dn42-control", "comment": None},
    ]
    have = admin("GET", f"/api/v1/admin/dns-groups/{gid}/zones/{zid}/records")
    assert isinstance(have, list)
    if not have:
        for rec in records:
            admin(
                "POST",
                f"/api/v1/admin/dns-groups/{gid}/zones/{zid}/records",
                {"ttl": None, "enabled": True, **rec},
            )

    # reverse zone (declares authority; SOA auto-derived)
    if not any(z["zone"] == "0.20.172.in-addr.arpa" for z in zones):
        admin(
            "POST",
            f"/api/v1/admin/dns-groups/{gid}/zones",
            {"zone": "0.20.172.in-addr.arpa", "enabled": True},
        )

    admin("PUT", f"/api/v1/admin/nodes/{NODE}/dns-group", {"dns_group_id": gid})
    print(f"  created DNS group ng-anycast (gid={gid}) + zones/records, subscribed {NODE}")


# --------------------------------------------------------------------------- #
# 5) Enrollment token + a pending registration.
# --------------------------------------------------------------------------- #
def feed_enrollment_and_registration() -> None:
    tokens = admin("GET", "/api/v1/admin/enrollment-tokens")
    assert isinstance(tokens, list)
    if not tokens:
        admin(
            "POST",
            "/api/v1/admin/enrollment-tokens",
            {"description": "fleet onboarding ticket"},
        )
        print("  issued enrollment token")

    # an unknown node registering lands in the pending-approval queue
    regs = admin("GET", "/api/v1/admin/registrations")
    pending = regs.get("registrations", []) if isinstance(regs, dict) else []
    if not any(r.get("node_id") == "edge7" for r in pending):
        _call(
            "POST",
            "/api/v1/agent/register",
            {
                "enrollment_token": ENROLL_TOKEN,
                "requested_node_id": "edge7",
                "inventory": {
                    "hostname": "edge7.fra.dn42",
                    "os": "linux",
                    "arch": "aarch64",
                    "kernel": "6.12.0-arm64",
                    "container_runtime": "docker",
                    "has_systemd": True,
                    "capabilities": ["docker", "wireguard", "bird"],
                },
            },
        )
        print("  enqueued pending registration for edge7")


def main() -> None:
    print(f"seeding doc-shot demo data into {API} (node={NODE})")
    agent_token = provision_node()
    feed_status(agent_token)
    feed_routing(agent_token)
    feed_dns()
    feed_enrollment_and_registration()
    print("done")


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        raise
