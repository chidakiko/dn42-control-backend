from __future__ import annotations

"""P0-P2 新增控制面能力的集成测试。

覆盖：
- 节点健康持久化 + ``/admin/health`` / ``/admin/nodes/{id}/health`` / status-events。
- Agent token 哈希 / 过期 / 轮换 + 固定字面量 token 的哈希登记。
- 待审批注册流（unknown 节点 -> pending -> approve/reject）。
"""

from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# -------- 健康持久化 --------

def test_snapshot_report_apply_persist_and_health(
    client: TestClient, config: ControlServerConfig
) -> None:
    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token

    snapshot = {
        "node_id": node,
        "generation": 1,
        "captured_at": "2025-01-01T00:00:00Z",
        "containers": [],
        "interfaces": [],
    }
    assert client.post(
        "/api/v1/agent/runtime-snapshot", headers=_auth(token), json=snapshot
    ).status_code == 200

    report = {
        "node_id": node,
        "desired_generation": 1,
        "observed_generation": 1,
        "status": "succeeded",
        "captured_at": "2025-01-01T00:00:05Z",
        "drift": [],
    }
    assert client.post(
        "/api/v1/agent/reconciliation-report", headers=_auth(token), json=report
    ).status_code == 200

    apply_result = {
        "node_id": node,
        "generation": 1,
        "status": "succeeded",
        "started_at": "2025-01-01T00:00:00Z",
        "finished_at": "2025-01-01T00:00:05Z",
    }
    assert client.post(
        "/api/v1/agent/apply-result", headers=_auth(token), json=apply_result
    ).status_code == 200

    # 单节点健康
    health = client.get(f"/api/v1/admin/nodes/{node}/health")
    assert health.status_code == 200
    body = health.json()
    assert body["health"] == "ok"
    assert body["desired_generation"] == 1
    assert body["observed_generation"] == 1
    assert body["last_report_status"] == "succeeded"
    assert body["last_snapshot"] is not None

    # fleet 健康
    fleet = client.get("/api/v1/admin/health").json()
    assert fleet["summary"].get("ok") == 1
    assert any(n["node_id"] == node for n in fleet["nodes"])

    # 历史事件
    events = client.get(
        f"/api/v1/admin/nodes/{node}/status-events", params={"kind": "report"}
    ).json()
    assert events["events"]
    assert events["events"][0]["kind"] == "report"


def test_health_degraded_on_drift(
    client: TestClient, config: ControlServerConfig
) -> None:
    node = config.bootstrap_node_id
    token = config.bootstrap_agent_token
    report = {
        "node_id": node,
        "desired_generation": 1,
        "observed_generation": 1,
        "status": "degraded",
        "captured_at": "2025-01-01T00:00:05Z",
        "drift": [
            {
                "component": "container",
                "name": "dn42-bird-router",
                "severity": "warning",
                "message": "not running",
            }
        ],
    }
    assert client.post(
        "/api/v1/agent/reconciliation-report", headers=_auth(token), json=report
    ).status_code == 200
    body = client.get(f"/api/v1/admin/nodes/{node}/health").json()
    assert body["health"] == "degraded"
    assert body["drift_count"] == 1


def test_node_health_404_when_no_report(client: TestClient) -> None:
    assert client.get("/api/v1/admin/nodes/edge1/health").status_code == 404


# -------- token 哈希 / 过期 / 轮换 --------

def test_issued_token_lists_without_secret(
    client: TestClient, config: ControlServerConfig
) -> None:
    node = config.bootstrap_node_id
    issued = client.post(f"/api/v1/admin/nodes/{node}/agent-tokens", json={}).json()
    secret = issued["token"]
    assert "." in secret  # 新 token 形如 <id>.<secret>

    # 列表里只暴露非机密 id，不含 secret
    listing = client.get(f"/api/v1/admin/nodes/{node}/agent-tokens").json()
    ids = [row["token"] for row in listing]
    assert secret not in ids
    assert any(secret.startswith(row["token"]) for row in listing)
    assert all(row["secret"] is None for row in listing)


def test_literal_token_is_stored_hashed_but_usable(
    client: TestClient, config: ControlServerConfig
) -> None:
    """seed 的 bootstrap 固定字面量 token 只存哈希，但仍可直接用作 Bearer。"""

    r = client.get(
        "/api/v1/agent/desired-state", headers=_auth(config.bootstrap_agent_token)
    )
    assert r.status_code == 200

    # 数据库（经 admin 列表暴露）中不出现字面量本身，只有派生 id。
    listing = client.get(
        f"/api/v1/admin/nodes/{config.bootstrap_node_id}/agent-tokens"
    ).json()
    ids = [row["token"] for row in listing]
    assert config.bootstrap_agent_token not in ids
    assert all(row["secret"] is None for row in listing)


def test_token_rotation_invalidates_old(
    client: TestClient, config: ControlServerConfig
) -> None:
    node = config.bootstrap_node_id
    issued = client.post(f"/api/v1/admin/nodes/{node}/agent-tokens", json={}).json()
    old_secret = issued["token"]
    token_id = old_secret.split(".", 1)[0]

    # 旧 token 可用
    assert client.get(
        "/api/v1/agent/desired-state", headers=_auth(old_secret)
    ).status_code == 200

    rotated = client.post(f"/api/v1/admin/agent-tokens/{token_id}/rotate", json={})
    assert rotated.status_code == 201
    new_secret = rotated.json()["token"]
    assert new_secret != old_secret

    # 旧失效、新可用
    assert client.get(
        "/api/v1/agent/desired-state", headers=_auth(old_secret)
    ).status_code == 401
    assert client.get(
        "/api/v1/agent/desired-state", headers=_auth(new_secret)
    ).status_code == 200


def test_expired_token_rejected(
    client: TestClient, config: ControlServerConfig
) -> None:
    """直接用 TokenStore 签发一个已过期 token，应被 resolve 拒绝。"""

    import anyio
    from datetime import datetime, timedelta, timezone

    from app.db.engine import Database
    from app.services.tokens import TokenStore

    async def _check() -> None:
        database = Database(config.database_url)
        try:
            store = TokenStore(database)
            past = datetime.now(timezone.utc) - timedelta(seconds=5)
            issued = await store.issue_detailed(
                config.bootstrap_node_id, expires_at=past
            )
            assert await store.resolve(issued.secret) is None
            # 未过期的对照
            future = datetime.now(timezone.utc) + timedelta(hours=1)
            ok = await store.issue_detailed(
                config.bootstrap_node_id, expires_at=future
            )
            principal = await store.resolve(ok.secret)
            assert principal is not None
            assert principal.node_id == config.bootstrap_node_id
        finally:
            await database.dispose()

    anyio.run(_check)


# -------- 待审批注册 --------

def test_unknown_node_registration_goes_pending(
    client: TestClient, config: ControlServerConfig
) -> None:
    payload = {
        "enrollment_token": config.enrollment_token,
        "requested_node_id": "brand-new-node",
        "inventory": {
            "hostname": "newbox",
            "os": "linux",
            "arch": "x86_64",
        },
    }
    r = client.post("/api/v1/agent/register", json=payload)
    assert r.status_code == 200
    assert r.json()["status"] == "pending-approval"
    assert r.json()["agent_token"] is None

    pending = client.get("/api/v1/admin/registrations", params={"status": "pending"}).json()
    rows = pending["registrations"]
    assert any(row["requested_node_id"] == "brand-new-node" for row in rows)
    reg_id = next(
        row["id"] for row in rows if row["requested_node_id"] == "brand-new-node"
    )

    approved = client.post(f"/api/v1/admin/registrations/{reg_id}/approve", json={})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"


def test_reject_registration(client: TestClient, config: ControlServerConfig) -> None:
    payload = {
        "enrollment_token": config.enrollment_token,
        "requested_node_id": "reject-me",
        "inventory": {"hostname": "h", "os": "linux", "arch": "x86_64"},
    }
    client.post("/api/v1/agent/register", json=payload)
    rows = client.get("/api/v1/admin/registrations").json()["registrations"]
    reg_id = next(row["id"] for row in rows if row["requested_node_id"] == "reject-me")
    rejected = client.post(
        f"/api/v1/admin/registrations/{reg_id}/reject", json={"note": "nope"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["note"] == "nope"
