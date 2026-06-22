from __future__ import annotations

"""节点级 WireGuard 密钥托管 + 注册一致性校验 + 对端传播回归测试。

锁定不变量：
- ``GET /agent/recovery-public-key``：未配置 → configured=False；配置 → 回 PEM + 指纹。
- ``POST /agent/wireguard-keys`` 节点级三分支：首次 stored / 一致 matched / 冲突 rejected(409)。
- 冲突时整批回滚：被拒后节点公钥不得留在库里（无"部分接受"）。
- 公钥首次登记 → 自动传播：所有"对端是本节点"的接口 peer 公钥被回填、对端重新物化。
- 公钥/托管密文落 ``nodes`` 表（节点级），不进自身 DesiredState。
"""

from pathlib import Path

from fastapi.testclient import TestClient

from dn42_common import generate_recovery_keypair, generate_wireguard_keypair
from app.core.config import ControlServerConfig
from app.main import create_app

from ._seed_helper import seed_test_db

_IFACE = "as4242420001"  # bootstrap demo 节点自带的 WG 接口


def _report(client: TestClient, token: str, node_id: str, public_key: str, escrow=None):
    body = {"node_id": node_id, "public_key": public_key}
    if escrow is not None:
        body["private_key_escrow"] = escrow
    return client.post(
        "/api/v1/agent/wireguard-keys",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
    )


def _provision_clone(client: TestClient, node_id: str) -> None:
    """克隆 bootstrap 节点的 DesiredState，以新 node_id 走 /admin/provision。"""

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


def test_recovery_public_key_absent_by_default(client: TestClient, config: ControlServerConfig) -> None:
    r = client.get(
        "/api/v1/agent/recovery-public-key",
        headers={"Authorization": f"Bearer {config.bootstrap_agent_token}"},
    )
    assert r.status_code == 200
    assert r.json()["configured"] is False


def test_recovery_public_key_served_when_configured(tmp_path: Path) -> None:
    _private, public_pem = generate_recovery_keypair()
    config = ControlServerConfig(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'rec.db').as_posix()}",
        seed_bootstrap_node=True,
        admin_token="t",
        recovery_public_key_pem=public_pem.decode("ascii"),
    )
    seed_test_db(config)
    with TestClient(create_app(config)) as client:
        r = client.get(
            "/api/v1/agent/recovery-public-key",
            headers={"Authorization": f"Bearer {config.bootstrap_agent_token}"},
        )
        body = r.json()
        assert body["configured"] is True
        assert body["public_key_pem"].startswith("-----BEGIN PUBLIC KEY-----")
        assert body["fingerprint"].startswith("sha256:")


def test_key_report_stored_then_matched(client: TestClient, config: ControlServerConfig) -> None:
    token, node = config.bootstrap_agent_token, config.bootstrap_node_id
    _priv, pub = generate_wireguard_keypair()

    first = _report(client, token, node, pub)
    assert first.status_code == 200
    assert first.json()["status"] == "stored"

    again = _report(client, token, node, pub)
    assert again.status_code == 200
    assert again.json()["status"] == "matched"


def test_key_report_mismatch_is_strictly_rejected(
    client: TestClient, config: ControlServerConfig
) -> None:
    token, node = config.bootstrap_agent_token, config.bootstrap_node_id
    _p1, pub1 = generate_wireguard_keypair()
    _p2, pub2 = generate_wireguard_keypair()

    assert _report(client, token, node, pub1).status_code == 200

    conflict = _report(client, token, node, pub2)
    assert conflict.status_code == 409
    assert node in str(conflict.json()["detail"])

    # 回滚证明：被拒后记录仍是 pub1，再报 pub1 应是 matched（而非 stored）。
    assert _report(client, token, node, pub1).json()["status"] == "matched"


def test_key_report_requires_self_node(client: TestClient, config: ControlServerConfig) -> None:
    _priv, pub = generate_wireguard_keypair()
    r = _report(client, config.bootstrap_agent_token, "some-other-node", pub)
    assert r.status_code == 403


def test_public_key_propagates_to_internal_peer(
    client: TestClient, config: ControlServerConfig
) -> None:
    token, node_a = config.bootstrap_agent_token, config.bootstrap_node_id

    # 建一个内部对端 lab-b，并在 lab-b 上建一条"对端是 node_a"的 WG 接口。
    _provision_clone(client, "lab-b")
    peering = client.post(
        "/api/v1/admin/nodes/lab-b/peerings",
        json={"name": "to-a", "remote_asn": 4242420000, "remote_node_id": node_a, "is_internal": True},
    )
    assert peering.status_code == 201, peering.text
    peering_id = peering.json()["id"]

    _placeholder_priv, placeholder_pub = generate_wireguard_keypair()
    iface_spec = {
        "name": "wg-to-a",
        "kind": "wireguard",
        "private_key_ref": "secret://nodes/lab-b/wireguard/node-key",
        "wireguard_peer": {"public_key": placeholder_pub, "allowed_ips": []},
    }
    created = client.post(
        "/api/v1/admin/nodes/lab-b/interfaces",
        json={"spec": iface_spec, "peering_id": peering_id},
    )
    assert created.status_code == 201, created.text
    iface_id = created.json()["id"]

    # node_a 首次上报公钥 → 应传播到 lab-b。
    _priv_a, pub_a = generate_wireguard_keypair()
    resp = _report(client, token, node_a, pub_a)
    assert resp.status_code == 200
    assert resp.json()["status"] == "stored"
    assert "lab-b" in resp.json()["propagated_to"]

    # 单一真相源：公钥不再回填进 lab-b 接口的存储 spec（仍是占位值），而是 materialize
    # 时按 peering.remote_node_id 从 node_a 的 Node.wireguard_public_key 现取现填。
    iface = client.get(f"/api/v1/admin/interfaces/{iface_id}").json()
    assert iface["spec"]["wireguard_peer"]["public_key"] == placeholder_pub

    # lab-b 的已物化 DesiredState 里，该接口对端公钥已派生为 node_a 的真实公钥。
    desired = client.get("/api/v1/admin/nodes/lab-b/desired-state").json()
    wg = next(i for i in desired["interfaces"] if i["name"] == "wg-to-a")
    assert wg["wireguard_peer"]["public_key"] == pub_a
