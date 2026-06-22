from __future__ import annotations

"""``_split_base_template`` 单一真相源行为：被 DB 列覆盖的节点身份字段不应留在 base_template。"""

from dn42_schemas.testing import build_hkg1_example_state

from app.db.provision import _split_base_template

# materializer._node_payload 会用 nodes 表列无条件覆盖的字段——它们不该在 base_template 里留副本。
_OVERRIDDEN_NODE_FIELDS = {
    "node_id",
    "asn",
    "router_id",
    "loopback_ipv4",
    "loopback_ipv6",
    "ipv4_prefixes",
    "ipv6_prefixes",
}


def test_base_template_drops_db_backed_node_identity() -> None:
    base_template = _split_base_template(build_hkg1_example_state())

    node = base_template["node"]
    assert _OVERRIDDEN_NODE_FIELDS.isdisjoint(node), (
        "node identity fields are authoritative in nodes table columns and must not be "
        f"duplicated into base_template.node: {_OVERRIDDEN_NODE_FIELDS & set(node)}"
    )
    # 无 DB 列权威覆盖的字段（region）仍需保留，否则 _default_region 取不到。
    assert "region" in node


def test_base_template_strips_dns_section() -> None:
    base_template = _split_base_template(build_hkg1_example_state())
    assert base_template["dns"] is None
