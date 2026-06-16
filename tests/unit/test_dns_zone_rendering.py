from __future__ import annotations

"""CoreDNS zone 文件渲染（P2-1）测试。"""

import pytest
from pydantic import ValidationError

from dn42_schemas import DnsRecordSpec, DnsSpec, DnsZoneSpec
from dn42_schemas.testing import build_hkg1_example_state
from dn42_templates import render_desired_state, render_dns_zone, zone_file_name


def _zone_with_records() -> DnsZoneSpec:
    return DnsZoneSpec(
        zone="example.dn42",
        records_ref="zone://example.dn42",
        primary_ns="ns1.example.dn42.",
        admin_email="hostmaster.example.dn42.",
        records=[
            DnsRecordSpec(name="@", type="NS", value="ns1.example.dn42."),
            DnsRecordSpec(name="ns1", type="A", value="172.20.0.62"),
            DnsRecordSpec(name="www", type="aaaa", value="fdce:1111:2222::20", ttl=600),
        ],
    )


def test_render_dns_zone_emits_soa_and_records() -> None:
    zone = _zone_with_records()

    rendered = render_dns_zone(zone, serial=7)

    assert "$ORIGIN example.dn42." in rendered
    assert "$TTL 3600" in rendered
    assert "@ IN SOA ns1.example.dn42. hostmaster.example.dn42. (" in rendered
    assert "7 ; serial" in rendered
    assert "@ IN NS ns1.example.dn42." in rendered
    assert "ns1 IN A 172.20.0.62" in rendered
    assert "www 600 IN AAAA fdce:1111:2222::20" in rendered


def test_render_desired_state_emits_zone_file_for_inline_records() -> None:
    state = build_hkg1_example_state()
    state = state.model_copy(
        update={
            "generation": 42,
            "dns": DnsSpec(
                bind_addresses=["172.20.0.20"],
                zones=[_zone_with_records()],
            ),
        }
    )

    rendered = render_desired_state(state)
    by_path = {file.path: file.content for file in rendered}

    assert "coredns/zones/db.example.dn42" in by_path
    assert "42 ; serial" in by_path["coredns/zones/db.example.dn42"]


def test_zones_without_records_emit_no_zone_file() -> None:
    state = build_hkg1_example_state()

    rendered = render_desired_state(state)
    paths = {file.path for file in rendered}

    assert not any(path.startswith("coredns/zones/") for path in paths)


def test_zone_file_name_matches_corefile_reference() -> None:
    zone = DnsZoneSpec(zone="0.20.172.in-addr.arpa", records_ref="zone://0.20.172")

    assert zone_file_name(zone) == "0.20.172"


def test_inline_records_require_soa_metadata() -> None:
    with pytest.raises(ValidationError):
        DnsZoneSpec(
            zone="example.dn42",
            records_ref="zone://example.dn42",
            records=[DnsRecordSpec(name="@", type="A", value="172.20.0.62")],
        )


def test_unknown_record_type_rejected() -> None:
    with pytest.raises(ValidationError):
        DnsRecordSpec(name="@", type="WKS", value="x")
