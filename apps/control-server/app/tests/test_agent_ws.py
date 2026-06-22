from __future__ import annotations

"""agent ↔ 控制面 WebSocket 事件通道的集成测试。

WebSocket 是控制面向 agent 推送 “desired_state 变更” / “请求上报快照” 等
事件的主要渠道。本文件锐意锁定以下行为：

* 未携带 Bearer / token 错误的 ws 连接会被以 close code 4401 拒绝，
  与 RFC 6455 close code 范围使用不冲突。
* 连上后立即收到 ``hello`` 消息 (含 node_id + 当前 generation)。
* admin 调用 ``POST /api/v1/admin/nodes/{node_id}/notify`` 下发
  ``desired_state_updated`` 事件会动夫递增 generation 并推送给 ws 订阅者；
  响应体包含 ``subscribers`` / ``delivered`` 计数；推送的 payload 含
  新的 generation。
* 同一接口的 ``snapshot_request`` 事件只是要求 agent 上报，
  不递增 generation；agent 收到原始 ``reason`` 透传。
"""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import ControlServerConfig


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_ws_rejects_missing_bearer(
    client: TestClient, config: ControlServerConfig
) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(f"/api/v1/agent/ws/{config.bootstrap_node_id}"):
            pass
    assert excinfo.value.code == 4401


def test_ws_rejects_invalid_bearer(
    client: TestClient, config: ControlServerConfig
) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            f"/api/v1/agent/ws/{config.bootstrap_node_id}",
            headers=_auth("not-a-real-token"),
        ):
            pass
    assert excinfo.value.code == 4401


def test_ws_node_path_accepts_matching_node(
    client: TestClient, config: ControlServerConfig
) -> None:
    """带 node_id 的规范路径：token 的 node 与路径一致时正常握手。"""

    with client.websocket_connect(
        f"/api/v1/agent/ws/{config.bootstrap_node_id}",
        headers=_auth(config.bootstrap_agent_token),
    ) as ws:
        hello = ws.receive_json()
        assert hello == {
            "type": "hello",
            "node_id": config.bootstrap_node_id,
            "generation": 1,
        }


def test_ws_node_path_rejects_mismatched_node(
    client: TestClient, config: ControlServerConfig
) -> None:
    """通道隔离：token 合法但路径里的 node 不属于它，以 4403 拒绝。"""

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/api/v1/agent/ws/some-other-node",
            headers=_auth(config.bootstrap_agent_token),
        ):
            pass
    assert excinfo.value.code == 4403


def test_ws_hello_then_event_after_notify(
    client: TestClient, config: ControlServerConfig
) -> None:
    with client.websocket_connect(
        f"/api/v1/agent/ws/{config.bootstrap_node_id}",
        headers=_auth(config.bootstrap_agent_token),
    ) as ws:
        hello = ws.receive_json()
        assert hello == {
            "type": "hello",
            "node_id": config.bootstrap_node_id,
            "generation": 1,
        }

        # 触发一次世代递增
        notify = client.post(
            f"/api/v1/admin/nodes/{config.bootstrap_node_id}/notify",
            json={"event": "desired_state_updated"},
        )
        assert notify.status_code == 200
        body = notify.json()
        assert body["generation"] == 2
        assert body["subscribers"] == 1
        assert body["delivered"] == 1

        event = ws.receive_json()
        assert event == {
            "type": "desired_state_updated",
            "generation": 2,
            "reason": "manual bump",
        }


def test_ws_snapshot_request_does_not_bump_generation(
    client: TestClient, config: ControlServerConfig
) -> None:
    with client.websocket_connect(
        f"/api/v1/agent/ws/{config.bootstrap_node_id}",
        headers=_auth(config.bootstrap_agent_token),
    ) as ws:
        ws.receive_json()  # hello

        notify = client.post(
            f"/api/v1/admin/nodes/{config.bootstrap_node_id}/notify",
            json={"event": "snapshot_request", "reason": "test"},
        )
        assert notify.status_code == 200
        assert notify.json()["generation"] == 1  # 不递增

        event = ws.receive_json()
        assert event == {"type": "snapshot_request", "reason": "test"}


def test_admin_notify_unknown_node_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/admin/nodes/ghost-node/notify",
        json={"event": "desired_state_updated"},
    )
    assert response.status_code == 404
