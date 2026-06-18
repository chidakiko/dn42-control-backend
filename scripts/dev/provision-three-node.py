from __future__ import annotations

"""把本地三节点 lab 的 DesiredState 灌入运行中的 Control Server。

用途：
- compose 部署里的一次性 provisioner 容器（control-server 起来后批量建节点）；
- 本地联调 / 集成测试的前置步骤。

它会：
1. 轮询 ``GET /healthz`` 等 control-server 就绪；
2. 用 ``build_local_three_node_states()`` 取三个 AS 内部节点
   （edge1 / edge2 / edge3，忽略外部 eBGP peer）；
3. 逐个 ``POST /api/v1/admin/provision``，并为每个节点绑定固定 agent token
   ``<node_id>-token``，方便对应 agent 用该 token 注册。

环境变量：
- ``CONTROL_URL``（默认 ``http://127.0.0.1:8000``）
- ``PROVISION_TIMEOUT``（等待就绪秒数，默认 60）
- ``ADMIN_TOKEN``（admin API Bearer；控制面配置了 ``DN42_CONTROL_ADMIN_TOKEN`` 时必填）
"""

import os
import sys
import time
from pathlib import Path

# 源码即包：把 packages 注入 sys.path，免去安装。
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _pkg in ("dn42_common", "dn42_schemas", "dn42_templates", "dn42_runtime"):
    _candidate = _REPO_ROOT / "packages" / _pkg
    if _candidate.exists() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import httpx

from dn42_schemas.testing import build_local_three_node_states

# 仅取三个 AS 内部节点；外部 eBGP peer 不进控制面。
_INTERNAL_NODE_IDS = {"edge1", "edge2", "edge3"}


def _wait_ready(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = client.get("/healthz", timeout=5.0)
            if resp.status_code == 200:
                return
        except Exception as exc:  # noqa: BLE001 - 起步阶段连接被拒很正常
            last_err = exc
        time.sleep(1.0)
    raise SystemExit(f"control-server 未在 {timeout:.0f}s 内就绪: {last_err}")


def main() -> int:
    control_url = os.environ.get("CONTROL_URL", "http://127.0.0.1:8000").rstrip("/")
    timeout = float(os.environ.get("PROVISION_TIMEOUT", "60"))
    admin_token = os.environ.get("ADMIN_TOKEN")
    headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}

    states = [
        state
        for _directory, state in build_local_three_node_states()
        if state.node.node_id in _INTERNAL_NODE_IDS
    ]
    if len(states) != len(_INTERNAL_NODE_IDS):
        found = sorted(s.node.node_id for s in states)
        raise SystemExit(f"期望 3 个内部节点，实际拿到: {found}")

    with httpx.Client(base_url=control_url, headers=headers) as client:
        _wait_ready(client, timeout)
        for state in states:
            node_id = state.node.node_id
            payload = {
                "state": state.model_dump(mode="json"),
                "agent_token": f"{node_id}-token",
            }
            resp = client.post("/api/v1/admin/provision", json=payload, timeout=30.0)
            if resp.status_code not in (200, 201):
                raise SystemExit(
                    f"provision {node_id} 失败: HTTP {resp.status_code} {resp.text}"
                )
            body = resp.json()
            print(
                f"provisioned {node_id}: generation={body['generation']} "
                f"subscribers={body['subscribers']} delivered={body['delivered']}"
            )

    print("三节点 provision 完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
