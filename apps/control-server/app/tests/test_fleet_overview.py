from __future__ import annotations

"""``GET /admin/fleet/overview`` 聚合端点单测。

该只读端点供 Web 仪表盘单次拉取整个 fleet：健康概览(同 ``/admin/health``) + 每节点
启用的服务角色 ``capabilities`` + 全网物理 WireGuard 邻接 ``links``(由各节点
``bird.internal_topology.igp_adjacencies`` 折叠成无向去重边)。

这里把 ``node_status`` 与 ``desired_state`` 两个 store 换成轻量 fake(与 test_health.py
换 ``app.state.database`` 同法)，从而精确控制每节点的角色与双向邻接接口，闭环验证
端点的去重 / 接口填充 / cost 取值逻辑，同时仍走真实 handler + response_model 校验。
"""

from fastapi.testclient import TestClient

from dn42_schemas import (
    IgpAdjacencySpec,
    RuntimeServiceSpec,
    ServiceRole,
)
from dn42_schemas.testing import build_hkg1_example_state


def _state_for(node_id: str, *, roles: list[ServiceRole], adjacencies):
    """以 HKG1 黄金样本为骨架，替换 node_id / 服务角色集合 / IGP 邻接。

    服务名按角色去重命名即可(端点只读 ``role.value``)；``internal_topology.hosts``
    沿用样本(已含 edge1/edge2)，仅替换 ``igp_adjacencies``。
    """

    base = build_hkg1_example_state()
    services = [
        RuntimeServiceSpec(name=f"dn42-{role.value}", role=role) for role in roles
    ]
    runtime = base.runtime.model_copy(update={"services": services})
    topology = base.bird.internal_topology.model_copy(
        update={"igp_adjacencies": adjacencies}
    )
    bird = base.bird.model_copy(update={"internal_topology": topology})
    node = base.node.model_copy(update={"node_id": node_id})
    # dns 段会驱动归一化重新注入/剥离 DNS 服务，这里清掉以让 services 原样保留。
    return base.model_copy(
        update={"node": node, "runtime": runtime, "bird": bird, "dns": None}
    )


class _FakeNodeStatus:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def list_all(self, **_kwargs) -> list[dict]:
        return self._rows


class _FakeDesiredState:
    def __init__(self, states: dict) -> None:
        self._states = states

    async def get(self, node_id: str):
        return self._states.get(node_id)


def _row(node_id: str, health: str) -> dict:
    return {
        "node_id": node_id,
        "health": health,
        "desired_generation": 1,
        "observed_generation": 1,
        "last_report_status": "succeeded",
        "last_apply_status": "succeeded",
        "drift_count": 0,
        "last_snapshot_at": None,
        "last_report_at": None,
        "last_apply_at": None,
        "updated_at": None,
    }


def test_fleet_overview_aggregates_capabilities_and_links(client: TestClient) -> None:
    # edge1: 四件套；edge2: bird + dns + rpki-cache。两侧 IGP 邻接各带不同接口名。
    states = {
        "edge1": _state_for(
            "edge1",
            roles=[
                ServiceRole.ROUTER_NETNS,
                ServiceRole.WG_GATEWAY,
                ServiceRole.BIRD_ROUTER,
                ServiceRole.RPKI_CACHE,
            ],
            adjacencies=[
                IgpAdjacencySpec(node="edge2", cost=100, interface="wg-edge2")
            ],
        ),
        "edge2": _state_for(
            "edge2",
            roles=[
                ServiceRole.BIRD_ROUTER,
                ServiceRole.DNS,
                ServiceRole.RPKI_CACHE,
            ],
            adjacencies=[
                # 回指 edge1：接口名不同(填到 b 侧)，cost 为空(不应覆盖 a 侧的 100)。
                IgpAdjacencySpec(node="edge1", interface="wg-edge1"),
                IgpAdjacencySpec(node="edge2"),  # 自环，应被跳过
            ],
        ),
    }
    client.app.state.node_status = _FakeNodeStatus(
        [_row("edge1", "ok"), _row("edge2", "degraded")]
    )
    client.app.state.desired_state = _FakeDesiredState(states)

    resp = client.get("/api/v1/admin/fleet/overview")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # summary 同 /admin/health：按 health 计数。
    assert body["summary"] == {"ok": 1, "degraded": 1}

    nodes = {n["node_id"]: n for n in body["nodes"]}
    assert set(nodes) == {"edge1", "edge2"}
    # 保留原有 fleet-health 行字段。
    assert nodes["edge1"]["desired_generation"] == 1
    assert nodes["edge1"]["health"] == "ok"
    # capabilities = sorted({enabled svc.role.value})。
    assert nodes["edge1"]["capabilities"] == [
        "bird-router",
        "router-netns",
        "rpki-cache",
        "wg-gateway",
    ]
    assert nodes["edge2"]["capabilities"] == ["bird-router", "dns", "rpki-cache"]

    # links：edge1<->edge2 去重成一条无向边，两侧接口都填上，cost 取非空(100)。
    assert len(body["links"]) == 1
    link = body["links"][0]
    assert link["a"] == "edge1" and link["b"] == "edge2"  # 字典序
    assert link["a_iface"] == "wg-edge2"  # edge1 侧
    assert link["b_iface"] == "wg-edge1"  # edge2 侧
    assert link["cost"] == 100


def test_fleet_overview_empty_capabilities_when_no_desired_state(
    client: TestClient,
) -> None:
    client.app.state.node_status = _FakeNodeStatus([_row("ghost", "unknown")])
    client.app.state.desired_state = _FakeDesiredState({})  # 无 desired state

    body = client.get("/api/v1/admin/fleet/overview").json()
    assert body["summary"] == {"unknown": 1}
    assert body["nodes"][0]["capabilities"] == []
    assert body["links"] == []
