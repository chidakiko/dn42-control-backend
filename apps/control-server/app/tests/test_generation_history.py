from __future__ import annotations

"""世代历史读取 / 对比 / 回滚端点集成测试。

seed 出的 ``edge1`` 启动即有 generation 1；PATCH 一次推进到 2，由此覆盖
读取单代快照、字段级 diff、回滚成新一代三条路径及其 404 / 400 边界。
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig


@pytest.fixture
def hkg1(config: ControlServerConfig) -> str:
    return config.bootstrap_node_id


def _bump_site(client: TestClient, node_id: str, site: str) -> int:
    """PATCH 一次触发重物化，返回新的 current_generation。"""

    r = client.patch(f"/api/v1/admin/nodes/{node_id}", json={"site": site})
    assert r.status_code == 200, r.text
    return r.json()["current_generation"]


def test_get_single_generation_returns_snapshot(client: TestClient, hkg1: str) -> None:
    r = client.get(f"/api/v1/admin/nodes/{hkg1}/generations/1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["generation"] == 1
    assert body["snapshot"]["generation"] == 1
    assert body["snapshot"]["node"]["node_id"] == hkg1


def test_get_unknown_generation_is_404(client: TestClient, hkg1: str) -> None:
    assert client.get(f"/api/v1/admin/nodes/{hkg1}/generations/999").status_code == 404


def test_get_generation_unknown_node_is_404(client: TestClient) -> None:
    assert client.get("/api/v1/admin/nodes/nope/generations/1").status_code == 404


def test_diff_reports_field_level_change(client: TestClient, hkg1: str) -> None:
    gen2 = _bump_site(client, hkg1, "diff-target-site")
    assert gen2 == 2

    r = client.get(f"/api/v1/admin/nodes/{hkg1}/generations/2/diff")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_generation"] == 1
    assert body["to_generation"] == 2
    assert body["changed"] is True
    site_changes = [c for c in body["changes"] if c["path"] == "node.site"]
    assert site_changes and site_changes[0]["new"] == "diff-target-site"


def test_diff_against_explicit_base(client: TestClient, hkg1: str) -> None:
    _bump_site(client, hkg1, "site-a")
    _bump_site(client, hkg1, "site-b")  # gen 3
    r = client.get(f"/api/v1/admin/nodes/{hkg1}/generations/3/diff?against=1")
    assert r.status_code == 200, r.text
    assert r.json()["from_generation"] == 1
    assert r.json()["changed"] is True


def test_diff_first_generation_without_base_is_400(client: TestClient, hkg1: str) -> None:
    assert client.get(f"/api/v1/admin/nodes/{hkg1}/generations/1/diff").status_code == 400


def test_rollback_republishes_old_snapshot_as_new_generation(
    client: TestClient, hkg1: str
) -> None:
    original = client.get(f"/api/v1/admin/nodes/{hkg1}/generations/1").json()["snapshot"]
    gen2 = _bump_site(client, hkg1, "changed-site")
    assert gen2 == 2

    r = client.post(f"/api/v1/admin/nodes/{hkg1}/generations/1/rollback")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_generation"] == 1
    assert body["new_generation"] == 3

    node = client.get(f"/api/v1/admin/nodes/{hkg1}").json()
    assert node["current_generation"] == 3

    rolled = client.get(f"/api/v1/admin/nodes/{hkg1}/generations/3").json()["snapshot"]
    # 新一代号 + 旧内容：site 拨回 gen1，generation 号推进到 3。
    assert rolled["generation"] == 3
    assert rolled["node"]["site"] == original["node"]["site"]

    # 回滚后 1↔3 在 site 上应无差异（除 generation 号本身）。
    diff = client.get(f"/api/v1/admin/nodes/{hkg1}/generations/3/diff?against=1").json()
    assert not [c for c in diff["changes"] if c["path"] == "node.site"]


def test_rollback_unknown_generation_is_404(client: TestClient, hkg1: str) -> None:
    assert (
        client.post(f"/api/v1/admin/nodes/{hkg1}/generations/999/rollback").status_code
        == 404
    )


def test_rollback_unknown_node_is_404(client: TestClient) -> None:
    assert client.post("/api/v1/admin/nodes/nope/generations/1/rollback").status_code == 404
