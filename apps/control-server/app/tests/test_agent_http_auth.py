from __future__ import annotations

"""控制面 agent HTTP 面的 Bearer-token 鉴权集成测试。

本文件以“需要鉴权的两个典型接口” 为切入点，锁定以下不变量：

* ``GET /api/v1/agent/desired-state``：缺业 Bearer、错 Bearer 都返回 401；
  带上 bootstrap node 的 token 后能返回初始 generation=1 的 DesiredState。
* ``POST /api/v1/agent/runtime-snapshot``：principal 必须与身份一致。
  token 对应的节点与 payload 中 ``node_id`` 不一致时返回 403；一致时
  返回 200。这避免 “节点 A 冗装 B 上报快照” 的伪造场景。
"""

from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_desired_state_requires_bearer(client: TestClient) -> None:
    response = client.get("/api/v1/agent/desired-state")
    assert response.status_code == 401


def test_desired_state_rejects_invalid_bearer(client: TestClient) -> None:
    response = client.get(
        "/api/v1/agent/desired-state",
        headers=_auth("not-a-real-token"),
    )
    assert response.status_code == 401


def test_desired_state_returns_state_for_bootstrap_token(
    client: TestClient, config: ControlServerConfig
) -> None:
    response = client.get(
        "/api/v1/agent/desired-state",
        headers=_auth(config.bootstrap_agent_token),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["node"]["node_id"] == config.bootstrap_node_id
    assert body["generation"] == 1


def test_runtime_snapshot_must_match_token_node(
    client: TestClient, config: ControlServerConfig
) -> None:
    # 先拿到合法 snapshot 模板
    state = client.get(
        "/api/v1/agent/desired-state",
        headers=_auth(config.bootstrap_agent_token),
    ).json()

    snapshot = {
        "node_id": "some-other-node",
        "generation": state["generation"],
        "captured_at": "2025-01-01T00:00:00Z",
        "containers": [],
        "interfaces": [],
    }
    response = client.post(
        "/api/v1/agent/runtime-snapshot",
        headers=_auth(config.bootstrap_agent_token),
        json=snapshot,
    )
    assert response.status_code == 403


def test_runtime_snapshot_accepted_for_self(
    client: TestClient, config: ControlServerConfig
) -> None:
    snapshot = {
        "node_id": config.bootstrap_node_id,
        "generation": 1,
        "captured_at": "2025-01-01T00:00:00Z",
        "containers": [],
        "interfaces": [],
    }
    response = client.post(
        "/api/v1/agent/runtime-snapshot",
        headers=_auth(config.bootstrap_agent_token),
        json=snapshot,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["node_id"] == config.bootstrap_node_id
