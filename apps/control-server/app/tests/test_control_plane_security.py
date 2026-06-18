from __future__ import annotations

"""控制面安全闭环回归测试。

锁定 P3 四项不变量：

* Admin API 鉴权：fail-closed（未配置 token 一律 403）；配置后无 / 错 Bearer
  一律 401，正确 Bearer 放行。
* enrollment token 按节点强制校验：表内门票绑定节点后只对该节点有效；
  一次性（消费后失效）；过期失效；通用门票任意节点可用。
* 注册审批状态强制校验：rejected 节点即使已 provision 也 403；pending 节点
  已 provision 也不发 token；approved 后才放行；approved 未 provision 的
  重复注册不会被重新入队为 pending。
* Admin 操作审计日志：写操作（含鉴权失败的尝试）落 ``admin_audit_log``，
  actor 记录鉴权主体。
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig
from app.main import create_app

_INVENTORY = {"hostname": "box", "os": "linux", "arch": "x86_64"}


def _register(client: TestClient, node_id: str, enrollment_token: str) -> dict:
    r = client.post(
        "/api/v1/agent/register",
        json={
            "enrollment_token": enrollment_token,
            "requested_node_id": node_id,
            "inventory": _INVENTORY,
        },
    )
    return {"status_code": r.status_code, "body": r.json()}


def _provision_node(client: TestClient, node_id: str) -> None:
    """克隆 bootstrap 节点的完整 DesiredState，以新 node_id 走 /admin/provision。"""

    app_config: ControlServerConfig = client.app.state.config  # type: ignore[attr-defined]
    state = client.get(
        "/api/v1/agent/desired-state",
        headers={"Authorization": f"Bearer {app_config.bootstrap_agent_token}"},
    ).json()
    bootstrap = state["node"]["node_id"]
    state["node"]["node_id"] = node_id
    topo = state["bird"]["internal_topology"]
    topo["routers"] = [node_id if r == bootstrap else r for r in topo["routers"]]
    topo["hosts"][node_id] = topo["hosts"].pop(bootstrap)
    r = client.post("/api/v1/admin/provision", json={"state": state})
    assert r.status_code == 201, r.text


def _registration_id(client: TestClient, node_id: str) -> int:
    rows = client.get("/api/v1/admin/registrations").json()["registrations"]
    return next(row["id"] for row in rows if row["requested_node_id"] == node_id)


# -------- Admin API 鉴权 --------

def test_admin_api_requires_bearer(client: TestClient) -> None:
    # 显式清掉默认 admin 头：无凭据必须 401。
    r = client.get("/api/v1/admin/nodes", headers={"Authorization": ""})
    assert r.status_code == 401


def test_admin_api_rejects_wrong_token(client: TestClient) -> None:
    r = client.get(
        "/api/v1/admin/nodes", headers={"Authorization": "Bearer wrong-token"}
    )
    assert r.status_code == 401


def test_admin_api_fail_closed_without_configured_token(tmp_path) -> None:
    config = ControlServerConfig(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'noadmin.db').as_posix()}",
        seed_bootstrap_node=True,
        admin_token=None,
    )
    with TestClient(create_app(config)) as client:
        r = client.get(
            "/api/v1/admin/nodes", headers={"Authorization": "Bearer anything"}
        )
        assert r.status_code == 403


def test_agent_token_cannot_access_admin_api(
    client: TestClient, config: ControlServerConfig
) -> None:
    r = client.get(
        "/api/v1/admin/nodes",
        headers={"Authorization": f"Bearer {config.bootstrap_agent_token}"},
    )
    assert r.status_code == 401


# -------- enrollment token 按节点强制校验 --------

def test_node_bound_enrollment_token_rejected_for_other_node(
    client: TestClient,
) -> None:
    _provision_node(client, "lab-a")
    _provision_node(client, "lab-b")
    secret = client.post(
        "/api/v1/admin/enrollment-tokens", json={"node_id": "lab-a"}
    ).json()["secret"]

    other = _register(client, "lab-b", secret)
    assert other["status_code"] == 401

    own = _register(client, "lab-a", secret)
    assert own["status_code"] == 200
    assert own["body"]["status"] == "accepted"
    assert own["body"]["agent_token"]


def test_enrollment_token_is_single_use(client: TestClient) -> None:
    _provision_node(client, "lab-once")
    secret = client.post(
        "/api/v1/admin/enrollment-tokens", json={"node_id": "lab-once"}
    ).json()["secret"]

    first = _register(client, "lab-once", secret)
    assert first["body"]["status"] == "accepted"

    again = _register(client, "lab-once", secret)
    assert again["status_code"] == 401

    # 注册结果为 pending-approval 时门票不被消费（agent 之后还要拿它换 token）。
    pending_secret = client.post("/api/v1/admin/enrollment-tokens", json={}).json()[
        "secret"
    ]
    queued = _register(client, "unknown-node", pending_secret)
    assert queued["body"]["status"] == "pending-approval"
    queued_again = _register(client, "unknown-node", pending_secret)
    assert queued_again["status_code"] == 200


def test_expired_enrollment_token_rejected(client: TestClient) -> None:
    _provision_node(client, "lab-exp")
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    secret = client.post(
        "/api/v1/admin/enrollment-tokens",
        json={"node_id": "lab-exp", "expires_at": past},
    ).json()["secret"]
    r = _register(client, "lab-exp", secret)
    assert r["status_code"] == 401


def test_generic_enrollment_token_valid_for_any_node(client: TestClient) -> None:
    _provision_node(client, "lab-any")
    secret = client.post("/api/v1/admin/enrollment-tokens", json={}).json()["secret"]
    r = _register(client, "lab-any", secret)
    assert r["body"]["status"] == "accepted"


# -------- 注册审批状态强制校验 --------

def test_rejected_node_is_refused_even_after_provision(
    client: TestClient, config: ControlServerConfig
) -> None:
    assert config.enrollment_token is not None
    queued = _register(client, "evil-node", config.enrollment_token)
    assert queued["body"]["status"] == "pending-approval"

    reg_id = _registration_id(client, "evil-node")
    assert (
        client.post(f"/api/v1/admin/registrations/{reg_id}/reject", json={}).status_code
        == 200
    )

    # 即使管理员（误）provision 了该节点，注册依旧显式拒绝。
    _provision_node(client, "evil-node")
    refused = _register(client, "evil-node", config.enrollment_token)
    assert refused["status_code"] == 403


def test_pending_node_gets_no_token_even_after_provision(
    client: TestClient, config: ControlServerConfig
) -> None:
    assert config.enrollment_token is not None
    _register(client, "wait-node", config.enrollment_token)
    _provision_node(client, "wait-node")

    still_pending = _register(client, "wait-node", config.enrollment_token)
    assert still_pending["body"]["status"] == "pending-approval"
    assert still_pending["body"]["agent_token"] is None

    reg_id = _registration_id(client, "wait-node")
    client.post(f"/api/v1/admin/registrations/{reg_id}/approve", json={})

    accepted = _register(client, "wait-node", config.enrollment_token)
    assert accepted["body"]["status"] == "accepted"
    assert accepted["body"]["agent_token"]


def test_approved_unprovisioned_node_is_not_requeued(
    client: TestClient, config: ControlServerConfig
) -> None:
    assert config.enrollment_token is not None
    _register(client, "appr-node", config.enrollment_token)
    reg_id = _registration_id(client, "appr-node")
    client.post(f"/api/v1/admin/registrations/{reg_id}/approve", json={})

    # 尚未 provision，重复注册不能把 approved 状态顶回 pending。
    again = _register(client, "appr-node", config.enrollment_token)
    assert again["body"]["status"] == "pending-approval"
    assert "awaiting admin provision" in again["body"]["message"]
    rows = client.get("/api/v1/admin/registrations").json()["registrations"]
    statuses = [
        row["status"] for row in rows if row["requested_node_id"] == "appr-node"
    ]
    assert statuses == ["approved"]


# -------- Admin 操作审计日志 --------

def test_admin_writes_are_audited_with_actor(client: TestClient) -> None:
    _provision_node(client, "audit-node")

    entries = client.get("/api/v1/admin/audit-log").json()["entries"]
    created = next(e for e in entries if e["path"] == "/api/v1/admin/provision")
    assert created["method"] == "POST"
    assert created["status_code"] == 201
    assert created["actor"] == "admin"


def test_failed_admin_write_attempt_is_audited(client: TestClient) -> None:
    r = client.post(
        "/api/v1/admin/nodes",
        json={"node_id": "x"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401

    entries = client.get("/api/v1/admin/audit-log").json()["entries"]
    attempt = next(e for e in entries if e["status_code"] == 401)
    assert attempt["actor"] is None
    assert attempt["path"] == "/api/v1/admin/nodes"


def test_admin_reads_are_not_audited(client: TestClient) -> None:
    client.get("/api/v1/admin/nodes")
    entries = client.get("/api/v1/admin/audit-log").json()["entries"]
    assert all(e["method"] != "GET" for e in entries)
