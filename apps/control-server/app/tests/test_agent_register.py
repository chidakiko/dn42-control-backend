from __future__ import annotations

"""控制面 ``POST /api/v1/agent/register`` 注册接口的集成测试。

该接口是节点首次上线拿 token 的渠道，本文件锁定三条分支：

* 携带正确 ``enrollment_token`` + ``requested_node_id`` 差异与 bootstrap
  节点 (``edge1``) 一致 → 返回 ``status=accepted``，及预置的
  ``bootstrap_agent_token`` 与 ``desired_state_generation=1``；agent 可直接
  拿这个 token 继续调用其他 agent 接口。
* 携带正确的 enrollment、但请求未知的 ``requested_node_id`` →
  ``status=pending-approval``、``agent_token=null``，进入人工审批队列。
* 错误的 ``enrollment_token`` → 401，不进入任何入队逻辑。
"""

from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig

_INVENTORY = {
    "hostname": "edge1.example.test",
    "os": "debian",
    "arch": "x86_64",
}


def test_register_with_bootstrap_node_returns_seed_token(
    client: TestClient, config: ControlServerConfig
) -> None:
    response = client.post(
        "/api/v1/agent/register",
        json={
            "enrollment_token": config.enrollment_token,
            "requested_node_id": config.bootstrap_node_id,
            "inventory": _INVENTORY,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "accepted"
    assert body["node_id"] == config.bootstrap_node_id
    assert body["agent_token"] == config.bootstrap_agent_token
    assert body["desired_state_generation"] == 1


def test_register_node_without_published_generation_returns_pending(
    client: TestClient, config: ControlServerConfig
) -> None:
    """节点已建于 Node 表但还没物化第一代（current_generation==0）时,
    register 必须回 PENDING_APPROVAL,而不是构造出 generation=None 的 ACCEPTED
    触发 schema 校验 500(回归锁)。"""

    # 直接建一个节点（current_generation=0,未加接口、未 provision）。
    created = client.post(
        "/api/v1/admin/nodes",
        json={"node_id": "fresh-node", "asn": 4242420099, "router_id": "172.20.99.1"},
    )
    assert created.status_code == 201
    assert created.json()["current_generation"] == 0

    response = client.post(
        "/api/v1/agent/register",
        json={
            "enrollment_token": config.enrollment_token,
            "requested_node_id": "fresh-node",
            "inventory": _INVENTORY,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending-approval"
    assert body["agent_token"] is None
    assert body["desired_state_generation"] is None


def test_register_unknown_node_returns_pending(
    client: TestClient, config: ControlServerConfig
) -> None:
    response = client.post(
        "/api/v1/agent/register",
        json={
            "enrollment_token": config.enrollment_token,
            "requested_node_id": "no-such-node",
            "inventory": _INVENTORY,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending-approval"
    assert body["agent_token"] is None


def test_register_rejects_bad_enrollment_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/agent/register",
        json={
            "enrollment_token": "totally-wrong",
            "requested_node_id": "edge1",
            "inventory": _INVENTORY,
        },
    )
    assert response.status_code == 401
