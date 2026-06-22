from __future__ import annotations

"""Admin CRUD 接口集成测试。

每条用例独占一份 TestClient -> SQLite，启动时已 seed 出 ``edge1``
节点和 ``mvp-agent-token``，因此 CRUD 行为可以直接观察到 generation 递增。
"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import ControlServerConfig


@pytest.fixture
def hkg1(config: ControlServerConfig) -> str:
    return config.bootstrap_node_id


def _interface_payload(name: str = "wg-peer-test") -> dict:
    return {
        "spec": {
            "name": name,
            "kind": "wireguard",
            "addresses": ["172.20.0.62/32"],
            "peer_routes": ["172.20.0.1/32"],
            "private_key_ref": "secret://test/private",
            "wireguard_peer": {
                "public_key": "+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=",
                "allowed_ips": ["172.20.0.1/32"],
            },
        }
    }


def _session_payload(name: str = "test-peer-v4", interface: str = "wg-peer-test") -> dict:
    return {
        "spec": {
            "name": name,
            "remote_asn": 4242420099,
            "neighbor": "172.20.0.1",
            "source_address": "172.20.0.62",
            "address_family": "ipv4",
            "interface": interface,
        }
    }


# -------- Node CRUD --------


def test_list_nodes_includes_bootstrap(client: TestClient, hkg1: str) -> None:
    resp = client.get("/api/v1/admin/nodes")
    assert resp.status_code == 200
    ids = [n["node_id"] for n in resp.json()]
    assert hkg1 in ids


def test_create_get_delete_node(client: TestClient) -> None:
    payload = {
        "node_id": "lab1",
        "asn": 4242420099,
        "router_id": "172.20.99.1",
        "ipv4_prefixes": ["172.20.99.0/26"],
        "loopback_ipv4": "172.20.99.1",
    }
    r = client.post("/api/v1/admin/nodes", json=payload)
    assert r.status_code == 201
    assert r.json()["current_generation"] == 0

    r = client.get("/api/v1/admin/nodes/lab1")
    assert r.status_code == 200
    assert r.json()["asn"] == 4242420099

    r = client.delete("/api/v1/admin/nodes/lab1")
    assert r.status_code == 204

    assert client.get("/api/v1/admin/nodes/lab1").status_code == 404


def test_node_link_local_create_patch_and_validation(client: TestClient) -> None:
    # 创建时带 link_local → 读回一致。
    r = client.post(
        "/api/v1/admin/nodes",
        json={
            "node_id": "lab2",
            "asn": 4242420098,
            "router_id": "172.20.98.1",
            "link_local": "fe80::28",
        },
    )
    assert r.status_code == 201
    assert r.json()["link_local"] == "fe80::28"
    assert client.get("/api/v1/admin/nodes/lab2").json()["link_local"] == "fe80::28"

    # PATCH 改 link_local。
    r = client.patch("/api/v1/admin/nodes/lab2", json={"link_local": "fe80::29"})
    assert r.status_code == 200 and r.json()["link_local"] == "fe80::29"

    # 非法值（非 fe80::/10）被校验拒绝。
    r = client.patch("/api/v1/admin/nodes/lab2", json={"link_local": "2001:db8::1"})
    assert r.status_code == 422
    r = client.post(
        "/api/v1/admin/nodes",
        json={"node_id": "lab3", "asn": 1, "router_id": "1.1.1.1", "link_local": "not-an-ip"},
    )
    assert r.status_code == 422

    client.delete("/api/v1/admin/nodes/lab2")


def test_create_node_conflict(client: TestClient, hkg1: str) -> None:
    r = client.post(
        "/api/v1/admin/nodes",
        json={"node_id": hkg1, "asn": 1, "router_id": "1.1.1.1"},
    )
    assert r.status_code == 409


def test_list_generations(client: TestClient, hkg1: str) -> None:
    r = client.get(f"/api/v1/admin/nodes/{hkg1}/generations")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    assert rows[0]["generation"] >= 1


def test_patch_node_bumps_generation_and_publishes(client: TestClient, hkg1: str) -> None:
    """PATCH 已发布节点必须真正推进世代并发布(回归锁)。

    曾经 update_node 在 materialize 后 session.refresh(node) 会回退内存里的
    current_generation,造成孤儿 generation 行 + 节点仍指向旧代,agent 永远
    拉不到新配置。
    """

    before = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]

    r = client.patch(f"/api/v1/admin/nodes/{hkg1}", json={"site": "relocated"})
    assert r.status_code == 200
    assert r.json()["current_generation"] == before + 1  # 响应里就是新代

    node = client.get(f"/api/v1/admin/nodes/{hkg1}").json()
    assert node["current_generation"] == before + 1  # 持久化确实推进
    assert node["site"] == "relocated"
    # generation 历史与 current 一致(无孤儿行 / 无回退)。
    gens = client.get(f"/api/v1/admin/nodes/{hkg1}/generations").json()
    assert gens[0]["generation"] == before + 1


def test_decommission_node_publishes_empty_desired_state(client: TestClient, hkg1: str) -> None:
    """退役:发布一份无对端的 DesiredState(空 interfaces/bgp),节点停止宣告路由。"""

    before_state = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    assert before_state["interfaces"]  # hkg1 样例本有接口
    before_gen = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]

    r = client.post(f"/api/v1/admin/nodes/{hkg1}/decommission")
    assert r.status_code == 200, r.text
    assert r.json()["lifecycle"] == "decommissioned"

    after_gen = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]
    assert after_gen == before_gen + 1  # 退役下发了新一代

    state = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    assert state["interfaces"] == []
    assert state["bgp_sessions"] == []
    assert state["dns"] is None

    # recommission 恢复:接口/会话回到 DesiredState。
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/recommission")
    assert r.status_code == 200
    assert r.json()["lifecycle"] == "active"
    restored = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    assert restored["interfaces"]  # 恢复


def test_delete_live_node_requires_decommission_first(client: TestClient, hkg1: str) -> None:
    """防孤儿:已发布的 active 节点直接 DELETE 被拒(409),退役后才能删。"""

    r = client.delete(f"/api/v1/admin/nodes/{hkg1}")
    assert r.status_code == 409
    assert "decommission" in r.text

    # 退役后可删。
    client.post(f"/api/v1/admin/nodes/{hkg1}/decommission")
    r = client.delete(f"/api/v1/admin/nodes/{hkg1}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/admin/nodes/{hkg1}").status_code == 404


def test_provision_peering_creates_all_three_in_one_call(client: TestClient, hkg1: str) -> None:
    """一键化端点:一次建立 Peering + WgInterface + BgpSession 并推进一代。"""

    before = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]
    payload = {
        "peering": {"name": "demopeer", "remote_asn": 4242420001},
        "interface_spec": _interface_payload("wg-demopeer")["spec"],
        "bgp_spec": _session_payload("demopeer-v4", interface="wg-demopeer")["spec"],
    }
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/peerings/provision", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["peering"]["name"] == "demopeer"
    assert body["interface"]["name"] == "wg-demopeer"
    assert body["bgp_session"]["name"] == "demopeer-v4"

    after = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]
    assert after == before + 1  # 三者建立只推进一代

    # 三者都真实落库并互相关联。
    peering_id = body["peering"]["id"]
    assert body["interface"]["peering_id"] == peering_id
    assert body["bgp_session"]["peering_id"] == peering_id
    names = [i["name"] for i in client.get(f"/api/v1/admin/nodes/{hkg1}/interfaces").json()]
    assert "wg-demopeer" in names


def test_provision_peering_without_bgp_only_creates_interface(
    client: TestClient, hkg1: str
) -> None:
    """纯传输 peering:省略 bgp_spec 只建 Peering + 接口。"""

    payload = {
        "peering": {"name": "transport-only", "remote_asn": 4242420099},
        "interface_spec": _interface_payload("wg-transport")["spec"],
    }
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/peerings/provision", json=payload)
    assert r.status_code == 201, r.text
    assert r.json()["bgp_session"] is None


def test_provision_peering_conflict_rolls_back_everything(client: TestClient, hkg1: str) -> None:
    """接口名冲突时整笔回滚:Peering 也不应残留(同事务)。"""

    # 先占用接口名。
    client.post(f"/api/v1/admin/nodes/{hkg1}/interfaces", json=_interface_payload("wg-dup"))
    before_peerings = len(client.get(f"/api/v1/admin/nodes/{hkg1}/peerings").json())

    payload = {
        "peering": {"name": "dup-peering", "remote_asn": 4242420001},
        "interface_spec": _interface_payload("wg-dup")["spec"],  # 冲突
    }
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/peerings/provision", json=payload)
    assert r.status_code == 409, r.text
    # Peering 不应残留。
    after_peerings = client.get(f"/api/v1/admin/nodes/{hkg1}/peerings").json()
    assert len(after_peerings) == before_peerings
    assert not any(p["name"] == "dup-peering" for p in after_peerings)


def test_failed_materialize_rolls_back_crud_change(
    client: TestClient, hkg1: str, config: ControlServerConfig
) -> None:
    """CRUD 与 materialize 是同一个事务：物化失败必须整体回滚。

    手法：绕过 API 直接把节点 base_template 改坏（gateway 非法），让后续
    任何 materialize 都过不了 DesiredState 校验；然后 POST 一个本身合法的
    接口。期望 422，且业务表不残留接口、世代号不推进——曾经的两事务实现
    会把接口留在表里变成"不可发布状态"。
    """

    import asyncio
    import json

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Node

    before_gen = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]

    # 绕过 API 直接改库制造非法 base_template。用 SQLAlchemy ORM（而非 raw sqlite3）→
    # SQLite / PostgreSQL 通用，且 JSON 列序列化由模型类型处理。
    async def _corrupt_base_template() -> None:
        engine = create_async_engine(config.database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                node = await session.get(Node, hkg1)
                template = json.loads(json.dumps(node.base_template))  # 深拷贝，改后整体重赋触发更新
                template["runtime"]["underlay"]["gateway"] = "999.999.999.999"
                node.base_template = template
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_corrupt_base_template())

    r = client.post(
        f"/api/v1/admin/nodes/{hkg1}/interfaces",
        json=_interface_payload("wg-rollback-test"),
    )
    assert r.status_code == 422, r.text

    names = [i["name"] for i in client.get(f"/api/v1/admin/nodes/{hkg1}/interfaces").json()]
    assert "wg-rollback-test" not in names
    after_gen = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]
    assert after_gen == before_gen


# -------- Interfaces --------


def test_create_interface_bumps_generation(client: TestClient, hkg1: str) -> None:
    before = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/interfaces", json=_interface_payload())
    assert r.status_code == 201, r.text
    after = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]
    assert after == before + 1

    r = client.get(f"/api/v1/admin/nodes/{hkg1}/interfaces")
    names = [i["name"] for i in r.json()]
    assert "wg-peer-test" in names


def test_invalid_interface_spec_rejected(client: TestClient, hkg1: str) -> None:
    bad = {"spec": {"name": "bad", "kind": "wireguard"}}  # 缺 private_key_ref / peer
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/interfaces", json=bad)
    assert r.status_code == 422
    assert "InterfaceSpec" in r.text


def test_interface_disable_drops_from_snapshot(client: TestClient, hkg1: str) -> None:
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/interfaces", json=_interface_payload("opt-iface"))
    iface_id = r.json()["id"]

    snap = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    assert any(i["name"] == "opt-iface" for i in snap["interfaces"])

    r = client.patch(f"/api/v1/admin/interfaces/{iface_id}", json={"enabled": False})
    assert r.status_code == 200

    snap = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    assert not any(i["name"] == "opt-iface" for i in snap["interfaces"])


def test_delete_interface(client: TestClient, hkg1: str) -> None:
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/interfaces", json=_interface_payload("gone"))
    iface_id = r.json()["id"]
    r = client.delete(f"/api/v1/admin/interfaces/{iface_id}")
    assert r.status_code == 204
    assert client.get(f"/api/v1/admin/interfaces/{iface_id}").status_code == 404


# -------- BGP sessions --------


def test_create_bgp_session_requires_existing_interface(client: TestClient, hkg1: str) -> None:
    # 引用一个不存在的 interface → materialize 阶段会被 DesiredState.validate_references 拒绝。
    payload = _session_payload(name="x", interface="missing-iface")
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/bgp-sessions", json=payload)
    # session 本身合法,但 materialize 失败 → 422
    assert r.status_code == 422


def test_create_bgp_session_happy_path(client: TestClient, hkg1: str) -> None:
    client.post(f"/api/v1/admin/nodes/{hkg1}/interfaces", json=_interface_payload("wg-test2"))
    payload = _session_payload(name="peer2-v4", interface="wg-test2")
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/bgp-sessions", json=payload)
    assert r.status_code == 201, r.text

    snap = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    assert any(s["name"] == "peer2-v4" for s in snap["bgp_sessions"])


# -------- DNS groups（共享 / anycast） --------


def _group_payload(name: str = "lab-dns") -> dict:
    return {
        "name": name,
        "bind_addresses": ["172.20.0.20", "fdce:1111:2222::20"],
        "cache_ttl_seconds": 300,
        "forwards": [],
    }


def _make_group(
    client: TestClient, name: str, zone: str = "lab.dn42", *, with_record: bool = True
) -> tuple[int, int]:
    """建组 + 一个权威 zone（默认再加一条记录，否则空 zone 不进 snapshot）。返回 (gid, zid)。"""

    gid = client.post("/api/v1/admin/dns-groups", json=_group_payload(name)).json()["id"]
    zid = client.post(f"/api/v1/admin/dns-groups/{gid}/zones", json={"zone": zone}).json()["id"]
    if with_record:
        r = client.post(
            f"/api/v1/admin/dns-groups/{gid}/zones/{zid}/records",
            json={"name": "@", "type": "A", "content": "172.20.0.20"},
        )
        assert r.status_code == 201, r.text
    return gid, zid


def _zone_in_snap(snap: dict, zone: str) -> dict | None:
    if not snap.get("dns"):
        return None
    return next((z for z in snap["dns"]["zones"] if z["zone"] == zone), None)


def test_dns_group_assign_deploys_dns_records_and_coredns(client: TestClient, hkg1: str) -> None:
    gid, _ = _make_group(client, "lab-dns", "lab.dn42")
    r = client.put(f"/api/v1/admin/nodes/{hkg1}/dns-group", json={"dns_group_id": gid})
    assert r.status_code == 200, r.text

    snap = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    assert snap["dns"] is not None
    assert "172.20.0.20" in snap["dns"]["bind_addresses"]
    zone = _zone_in_snap(snap, "lab.dn42")
    assert zone is not None
    # 自动 SOA。
    assert zone["primary_ns"] == "ns.lab.dn42."
    assert zone["admin_email"] == "hostmaster.lab.dn42."
    # 记录组装进 zone（content -> value）。
    assert any(
        rec["name"] == "@" and rec["type"] == "A" and rec["value"] == "172.20.0.20"
        for rec in zone["records"]
    )
    # 分配组即启用：coredns 服务被注入。
    assert any(s["role"] == "dns" for s in snap["runtime"]["services"])


def test_dns_group_unassign_removes_dns(client: TestClient, hkg1: str) -> None:
    gid, _ = _make_group(client, "tmp-dns")
    client.put(f"/api/v1/admin/nodes/{hkg1}/dns-group", json={"dns_group_id": gid})
    r = client.put(f"/api/v1/admin/nodes/{hkg1}/dns-group", json={"dns_group_id": None})
    assert r.status_code == 200

    snap = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    assert snap["dns"] is None
    assert not any(s["role"] == "dns" for s in snap["runtime"]["services"])


def test_dns_empty_zone_not_served(client: TestClient, hkg1: str) -> None:
    """没有记录的 zone 不进 snapshot（避免 Corefile 引用却无 zone 文件）。"""

    gid, _ = _make_group(client, "empty-z", "empty.dn42", with_record=False)
    client.put(f"/api/v1/admin/nodes/{hkg1}/dns-group", json={"dns_group_id": gid})
    snap = client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json()
    # 组里只有空 zone、无 forwards ⇒ 整段 dns 为 None。
    assert snap["dns"] is None


def test_dns_disabled_record_excluded(client: TestClient, hkg1: str) -> None:
    gid, zid = _make_group(client, "dz", "lab.dn42")
    rid = client.post(
        f"/api/v1/admin/dns-groups/{gid}/zones/{zid}/records",
        json={"name": "www", "type": "AAAA", "content": "fdce:1111:2222::99"},
    ).json()["id"]
    client.patch(
        f"/api/v1/admin/dns-groups/{gid}/zones/{zid}/records/{rid}", json={"enabled": False}
    )
    client.put(f"/api/v1/admin/nodes/{hkg1}/dns-group", json={"dns_group_id": gid})

    zone = _zone_in_snap(client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json(), "lab.dn42")
    assert zone is not None
    assert not any(rec["name"] == "www" for rec in zone["records"])


def test_dns_ptr_record_for_rdns(client: TestClient, hkg1: str) -> None:
    """rDNS = 反向 zone 下的 PTR 记录。"""

    gid, zid = _make_group(client, "rdns", "20.172.in-addr.arpa", with_record=False)
    r = client.post(
        f"/api/v1/admin/dns-groups/{gid}/zones/{zid}/records",
        json={"name": "1.0", "type": "PTR", "content": "gw.lab.dn42."},
    )
    assert r.status_code == 201, r.text
    client.put(f"/api/v1/admin/nodes/{hkg1}/dns-group", json={"dns_group_id": gid})

    zone = _zone_in_snap(
        client.get(f"/api/v1/admin/nodes/{hkg1}/desired-state").json(), "20.172.in-addr.arpa"
    )
    assert zone is not None
    assert any(rec["type"] == "PTR" and rec["value"] == "gw.lab.dn42." for rec in zone["records"])


def test_dns_record_change_rematerializes_member(client: TestClient, hkg1: str) -> None:
    """组内记录变更会重新物化全部成员节点——这正是多节点 DNS 同步（anycast）的机制。"""

    gid, zid = _make_group(client, "sync-dns", "a.dn42")
    client.put(f"/api/v1/admin/nodes/{hkg1}/dns-group", json={"dns_group_id": gid})
    gen_before = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]

    r = client.post(
        f"/api/v1/admin/dns-groups/{gid}/zones/{zid}/records",
        json={"name": "ns1", "type": "AAAA", "content": "fdce:1111:2222::1"},
    )
    assert r.status_code == 201
    gen_after = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]
    assert gen_after > gen_before


# -------- Peering --------


def test_peering_crud(client: TestClient, hkg1: str) -> None:
    r = client.post(
        f"/api/v1/admin/nodes/{hkg1}/peerings",
        json={"name": "demopeer", "remote_asn": 4242420001, "is_internal": False},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = client.patch(f"/api/v1/admin/peerings/{pid}", json={"notes": "via icvpn"})
    assert r.json()["notes"] == "via icvpn"

    r = client.get(f"/api/v1/admin/nodes/{hkg1}/peerings")
    assert any(p["name"] == "demopeer" for p in r.json())

    assert client.delete(f"/api/v1/admin/peerings/{pid}").status_code == 204


# -------- Peering: 组合读 / 全量 / 回填 --------


def test_get_peering_full_returns_children(client: TestClient, hkg1: str) -> None:
    """组合读:provision 后 GET /peerings/{id}/full 同时返回接口与会话。"""

    r = client.post(
        f"/api/v1/admin/nodes/{hkg1}/peerings/provision",
        json={
            "peering": {"name": "demopeer", "remote_asn": 4242420001},
            "interface_spec": _interface_payload("wg-demopeer")["spec"],
            "bgp_spec": _session_payload("demopeer-v4", interface="wg-demopeer")["spec"],
        },
    )
    pid = r.json()["peering"]["id"]

    full = client.get(f"/api/v1/admin/peerings/{pid}/full").json()
    assert full["name"] == "demopeer"
    assert [i["name"] for i in full["interfaces"]] == ["wg-demopeer"]
    assert [s["name"] for s in full["bgp_sessions"]] == ["demopeer-v4"]

    listing = client.get(f"/api/v1/admin/nodes/{hkg1}/peerings/full").json()
    assert any(p["id"] == pid and p["interfaces"] for p in listing)


def test_put_peering_full_create_then_replace(client: TestClient, hkg1: str) -> None:
    """全量端点:先建一接口/双会话,再 PUT 去掉一条 -> 旧子资源删除、推进世代。"""

    # 同一 wg 接口上的会话必须共用 remote_asn(DesiredState 跨字段校验)。
    asn = 4242420001
    v4 = {
        "name": "demopeer-v4",
        "remote_asn": asn,
        "neighbor": "172.20.0.1",
        "source_address": "172.20.0.62",
        "address_family": "ipv4",
        "interface": "wg-demopeer",
    }
    v6 = {
        "name": "demopeer-v6",
        "remote_asn": asn,
        "neighbor": "fe80::1%wg-demopeer",
        "source_address": "172.20.0.62",
        "address_family": "ipv6",
        "interface": "wg-demopeer",
    }

    before = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]
    body = {
        "peering": {"name": "demopeer", "remote_asn": asn},
        "interfaces": [_interface_payload("wg-demopeer")],
        "bgp_sessions": [{"spec": v4}, {"spec": v6}],
    }
    r = client.put(f"/api/v1/admin/nodes/{hkg1}/peerings/full", json=body)
    assert r.status_code == 200, r.text
    pid = r.json()["peering"]["id"]
    assert len(r.json()["peering"]["bgp_sessions"]) == 2
    assert client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"] == before + 1

    # 再次 PUT 只留 v4 会话 -> v6 被删,接口保留,世代再 +1。
    body["bgp_sessions"] = [{"spec": v4}]
    r2 = client.put(f"/api/v1/admin/nodes/{hkg1}/peerings/full", json=body)
    assert r2.status_code == 200, r2.text
    assert r2.json()["peering"]["id"] == pid  # 同名 -> upsert 同一行
    assert [s["name"] for s in r2.json()["peering"]["bgp_sessions"]] == ["demopeer-v4"]
    names = [s["name"] for s in client.get(f"/api/v1/admin/nodes/{hkg1}/bgp-sessions").json()]
    assert "demopeer-v6" not in names


def _plan_named(created: list[dict], name: str) -> dict:
    return next(p for p in created if p["name"] == name)


def test_backfill_groups_orphans_into_peerings(client: TestClient, hkg1: str) -> None:
    """回填:孤儿接口+会话 -> 自动建 Peering 并回填 peering_id;dry-run 不写库;幂等。

    seed 节点本身就带孤儿配置,故只针对本用例新建的 wg-demopeer 组做断言。
    """

    # 用单资源端点造孤儿:接口 + 锚定它的会话(peering_id 默认 None)。
    client.post(f"/api/v1/admin/nodes/{hkg1}/interfaces", json=_interface_payload("wg-demopeer"))
    client.post(
        f"/api/v1/admin/nodes/{hkg1}/bgp-sessions",
        json=_session_payload("demopeer-v4", interface="wg-demopeer"),
    )

    before_gen = client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"]

    # dry-run:返回计划但不建 Peering。
    preview = client.post(
        f"/api/v1/admin/nodes/{hkg1}/peerings/backfill", json={"dry_run": True}
    ).json()
    assert preview["dry_run"] is True
    plan = _plan_named(preview["created"], "wg-demopeer")
    assert plan["peering_id"] is None
    assert plan["remote_asn"] == 4242420099
    assert plan["interface_ids"] and plan["bgp_session_ids"]
    assert client.get(f"/api/v1/admin/nodes/{hkg1}/peerings").json() == []  # 未写库

    # apply:真实建 Peering 并回填,且不推进世代(peering_id 不进 DesiredState)。
    applied = client.post(
        f"/api/v1/admin/nodes/{hkg1}/peerings/backfill", json={"dry_run": False}
    ).json()
    new_pid = _plan_named(applied["created"], "wg-demopeer")["peering_id"]
    assert new_pid is not None
    assert client.get(f"/api/v1/admin/nodes/{hkg1}").json()["current_generation"] == before_gen

    full = client.get(f"/api/v1/admin/peerings/{new_pid}/full").json()
    assert [i["name"] for i in full["interfaces"]] == ["wg-demopeer"]
    assert [s["name"] for s in full["bgp_sessions"]] == ["demopeer-v4"]

    # 幂等:所有孤儿(含 seed 自带)均已纳管,重跑无新增。
    rerun = client.post(
        f"/api/v1/admin/nodes/{hkg1}/peerings/backfill", json={"dry_run": False}
    ).json()
    assert rerun["created"] == []


def test_backfill_skips_transport_only_interface(client: TestClient, hkg1: str) -> None:
    """纯传输接口(无 BGP 会话)推不出 ASN -> 跳过,保持孤儿。"""

    client.post(f"/api/v1/admin/nodes/{hkg1}/interfaces", json=_interface_payload("wg-transport"))
    result = client.post(
        f"/api/v1/admin/nodes/{hkg1}/peerings/backfill", json={"dry_run": False}
    ).json()
    assert not any(p["name"] == "wg-transport" for p in result["created"])
    assert any(s["name"] == "wg-transport" for s in result["skipped_interfaces"])
    # 接口仍未关联任何 peering。
    ifaces = client.get(f"/api/v1/admin/nodes/{hkg1}/interfaces").json()
    assert next(i for i in ifaces if i["name"] == "wg-transport")["peering_id"] is None


# -------- Tokens --------


def test_issue_and_revoke_agent_token(client: TestClient, hkg1: str) -> None:
    r = client.post(f"/api/v1/admin/nodes/{hkg1}/agent-tokens", json={})
    assert r.status_code == 201
    token = r.json()["token"]
    assert token

    # 新 token 应当能直接用作 Bearer
    me = client.get("/api/v1/agent/desired-state", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200

    assert client.delete(f"/api/v1/admin/agent-tokens/{token}").status_code == 204
    me = client.get("/api/v1/agent/desired-state", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401


def test_enrollment_token_lifecycle(client: TestClient, hkg1: str) -> None:
    r = client.post("/api/v1/admin/enrollment-tokens", json={"description": "lab"})
    assert r.status_code == 201
    body = r.json()
    token_id = body["token_id"]
    assert token_id.startswith("ent_")
    # 明文 secret 仅创建响应可见
    assert body["secret"]

    r = client.get("/api/v1/admin/enrollment-tokens")
    listed = r.json()
    assert any(t["token_id"] == token_id for t in listed)
    assert all("secret" not in t for t in listed)

    assert client.delete(f"/api/v1/admin/enrollment-tokens/{token_id}").status_code == 204
