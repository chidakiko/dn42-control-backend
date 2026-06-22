#!/usr/bin/env python3
"""把节点本端 LLA 从各 WG 接口 addresses 里剥离，收敛为 NodeSpec.link_local 单源。

前提：节点已设 `base_template.node.link_local`（如 `fe80::28`），且控制面 materializer
已支持把它派生回外部 eBGP WG 接口 addresses（dedup）。本脚本做配套的存量剥离：

  对该节点每条 WG 接口，若 addresses 含 `<link_local>/64`，PATCH 去掉它。

剥离后输出不变（materializer 现取现派生），但存储侧不再各存一份副本 ⇒ 真单源。
内部互联接口用各自 LL（如 fe80::14），不含 `<link_local>/64`，天然不受影响。

用法：
  DN42_ADMIN_TOKEN=... python scripts/tools/backfill_node_lla.py <control-url> <node-id> [--apply]
  不带 --apply 为 dry-run（只打印将改什么）。
"""
from __future__ import annotations

import os
import sys
import urllib.request
import json


def _api(method: str, url: str, token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv
    if len(args) != 2:
        print(__doc__)
        return 2
    base, node_id = args[0].rstrip("/"), args[1]
    token = os.environ.get("DN42_ADMIN_TOKEN", "")
    if not token:
        print("set DN42_ADMIN_TOKEN")
        return 2

    node = _api("GET", f"{base}/api/v1/admin/nodes/{node_id}", token)
    lla = (node.get("base_template", {}).get("node", {}) or {}).get("link_local")
    if not lla:
        print(f"node {node_id} has no base_template.node.link_local — set it first")
        return 1
    target = f"{lla}/64"
    print(f"node {node_id} link_local={lla} → stripping {target!r} from WG interface addresses")

    ifaces = _api("GET", f"{base}/api/v1/admin/nodes/{node_id}/interfaces", token)
    changed = 0
    for iface in ifaces:
        spec = iface.get("spec", {})
        if spec.get("kind") != "wireguard":
            continue
        addresses = list(spec.get("addresses") or [])
        if target not in addresses:
            continue
        new_addrs = [a for a in addresses if a != target]
        print(f"  {iface['name']} (id={iface['id']}): {addresses} -> {new_addrs}")
        changed += 1
        if apply:
            spec["addresses"] = new_addrs
            _api("PATCH", f"{base}/api/v1/admin/interfaces/{iface['id']}", token, {"spec": spec})
    print(f">> {'applied' if apply else 'dry-run'}: {changed} interface(s){'' if apply else ' would change'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
