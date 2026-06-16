from __future__ import annotations

"""控制面 ``/healthz`` 探活接口的单元测试。

Kubernetes / docker compose / 负载均衡需要一个不走鉴权但能反映服务可用性的
轻量接口。本文件锁定：

* 顶层 ``/healthz`` 是 readiness 探针：探 DB 连通性，正常返回 200，DB 不可达
  返回 503——避免 DB 挂了仍报健康；
* ``/api/v1/healthz`` 是 liveness 探针：只反映进程能响应。
"""

from fastapi.testclient import TestClient


def test_healthz_top_level_probes_db(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}


def test_healthz_returns_503_when_db_down(client: TestClient) -> None:
    class _BrokenDatabase:
        def session(self):
            raise RuntimeError("database is down")

    original = client.app.state.database
    client.app.state.database = _BrokenDatabase()
    try:
        response = client.get("/healthz")
    finally:
        client.app.state.database = original

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "database": "down"}


def test_healthz_under_v1_is_liveness(client: TestClient) -> None:
    response = client.get("/api/v1/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
