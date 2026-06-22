from __future__ import annotations

"""节点级 LLA（``NodeSpec.link_local``）派生到外部 eBGP WG 接口的单一真相源行为。

本端 `fe80::X/64` 原本各存一份在每条 eBGP 接口 addresses（副本）；收敛为 node.link_local
单源 + materializer 派生。这里锁住派生的范围（仅外部 eBGP，不含内部互联）与形式。
"""

from types import SimpleNamespace

from app.services.materializer import _interface_payload

_EXTERNAL = SimpleNamespace(is_internal=False, remote_node_id=None)
_INTERNAL = SimpleNamespace(is_internal=True, remote_node_id=None)


def _row(kind: str, addresses: list[str], peering=_EXTERNAL):
    return SimpleNamespace(
        spec={"name": "if", "kind": kind, "addresses": addresses}, peering=peering
    )


def test_lla_derived_onto_external_ebgp_wg() -> None:
    out = _interface_payload(_row("wireguard", ["172.20.0.1/32"]), {}, "fe80::28")
    assert "fe80::28/64" in out["addresses"]


def test_lla_dedup_when_already_present() -> None:
    out = _interface_payload(_row("wireguard", ["fe80::28/64"]), {}, "fe80::28")
    assert out["addresses"].count("fe80::28/64") == 1


def test_lla_not_added_to_internal_interconnect_wg() -> None:
    # 内部互联 WG 接口（is_internal=True）保留各自 LL，不该被加节点 LLA。
    out = _interface_payload(_row("wireguard", ["fe80::14/64"], peering=_INTERNAL), {}, "fe80::28")
    assert "fe80::28/64" not in out["addresses"]


def test_lla_not_added_without_peering() -> None:
    out = _interface_payload(_row("wireguard", [], peering=None), {}, "fe80::28")
    assert "fe80::28/64" not in out["addresses"]


def test_lla_not_added_to_non_wireguard() -> None:
    out = _interface_payload(_row("dummy", ["172.20.0.57/32"]), {}, "fe80::28")
    assert "fe80::28/64" not in out["addresses"]


def test_lla_noop_when_node_has_no_link_local() -> None:
    out = _interface_payload(_row("wireguard", []), {}, None)
    assert out["addresses"] == []
