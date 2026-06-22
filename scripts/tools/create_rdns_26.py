#!/usr/bin/env python3
"""部署含 allow_slash 校验器的控制面后，建 RFC2317 无类 /26 反向 zone。

前置：控制面与全 fleet agent 已部署放行 zone 名 `/` 的版本（domain.py allow_slash）。
用法：DN42_ADMIN_TOKEN=... python scripts/tools/create_rdns_26.py
幂等性：仅建一次（建前会查重）。控制面地址默认 https://control-server.example，可用 DN42_CP 覆盖。
"""
import json, os, urllib.request, urllib.error

TOK = os.environ["DN42_ADMIN_TOKEN"]
B = os.environ.get("DN42_CP", "https://control-server.example") + "/api/v1/admin"
GROUP = 1
ZONE = "0/26.0.20.172.in-addr.arpa"
# owner（/26 内主机八位组）-> PTR 目标，镜像 example.dn42 正向
PTRS = [
    ("2", "hkg2-transit.hkg.global.example.dn42."),
    ("55", "can2-backbone.mainland.example.dn42."),
    ("57", "ns1.example.dn42."),
    ("59", "pvg2-backbone.mainland.example.dn42."),
    ("62", "edge1.hkg.global.example.dn42."),
]


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(B + path, data=data, method=method,
        headers={"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


zones = req("GET", f"/dns-groups/{GROUP}/zones")[1]
existing = next((z for z in zones if z["zone"] == ZONE), None)
if existing:
    print(f"zone already exists id={existing['id']}; skipping create")
    zid = existing["id"]
else:
    s, z = req("POST", f"/dns-groups/{GROUP}/zones", {
        "zone": ZONE, "primary_ns": "ns1.example.dn42.", "admin_email": "admin.example.dn42.",
        "default_ttl": 300, "soa_refresh": 300, "soa_retry": 120,
        "soa_expire": 1209600, "soa_minimum": 300})
    print("POST zone:", s, z if s not in (200, 201) else f"id={z['id']}")
    if s not in (200, 201):
        raise SystemExit("zone create failed (control plane not yet on allow_slash build?)")
    zid = z["id"]

recs = [("@", "NS", "ns1.example.dn42.")] + [(n, "PTR", t) for n, t in PTRS]
ok = err = 0
for i, (n, t, c) in enumerate(recs):
    s, body = req("POST", f"/dns-groups/{GROUP}/zones/{zid}/records",
                  {"name": n, "type": t, "content": c, "sort_order": i})
    if s in (200, 201):
        ok += 1
    else:
        err += 1
        print("  REC FAIL", n, t, c, "->", s, body)
print(f"/26 reverse records: ok={ok} err={err}")
print("done. notify nodes to re-pull if needed.")
