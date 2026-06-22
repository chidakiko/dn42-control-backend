from __future__ import annotations

"""agent 上报类 schema 的单元测试：:class:`RuntimeSnapshot` 、
:class:`ReconciliationReport` 与 :class:`ApplyResult`。

phase C 决定了控制面如何看节点运行状况，本文件锐意锁定：

* 所有时间戳字段都使用 ISO-8601（接受 ``+00:00`` 与
  ``Z`` 后缀），``finished_at`` 可选（apply 未结束时为 None），
  ``ObservedBgpProtocol.since`` 同样可缺；非法字串一律拒绝。
* link-local 邻居必须以某种方式携带外发接口：在 ``neighbor`` 内嵌
  ``%zone``，或者在 ``BgpSessionSpec.interface`` 上显式提供；二者都
  没有会报 ``link-local``；zone 与 interface 同时提供但不一致也会
  产生 “zone and interface must match” 错误。
* 全局 IPv6 邻居不需要接口提示，避免 “全局 peer 必需接口” 这
  种过度限制。
"""

import pytest
from pydantic import ValidationError

from dn42_schemas.agent import (
    ApplyResult,
    ObservedBgpProtocol,
    ReconciliationReport,
    RuntimeSnapshot,
)
from dn42_schemas.enums import AddressFamily, ApplyStatus
from dn42_schemas.routing import BgpSessionSpec


VALID_TS = "2026-06-03T12:34:56+00:00"
VALID_TS_Z = "2026-06-03T12:34:56Z"


class TestTimestampFields:
    def test_apply_result_accepts_iso8601(self) -> None:
        result = ApplyResult(
            node_id="hkg1",
            generation=1,
            status=ApplyStatus.SUCCEEDED,
            started_at=VALID_TS,
            finished_at=VALID_TS_Z,
        )
        assert result.started_at == VALID_TS
        assert result.finished_at == VALID_TS_Z

    def test_apply_result_rejects_non_iso8601(self) -> None:
        with pytest.raises(ValidationError):
            ApplyResult(
                node_id="hkg1",
                generation=1,
                status=ApplyStatus.SUCCEEDED,
                started_at="yesterday",
            )

    def test_apply_result_finished_at_optional(self) -> None:
        result = ApplyResult(
            node_id="hkg1",
            generation=1,
            status=ApplyStatus.SUCCEEDED,
            started_at=VALID_TS,
        )
        assert result.finished_at is None

    def test_runtime_snapshot_validates_captured_at(self) -> None:
        with pytest.raises(ValidationError):
            RuntimeSnapshot(node_id="hkg1", captured_at="not-a-time")
        ok = RuntimeSnapshot(node_id="hkg1", captured_at=VALID_TS)
        assert ok.captured_at == VALID_TS

    def test_reconciliation_report_validates_captured_at(self) -> None:
        with pytest.raises(ValidationError):
            ReconciliationReport(
                node_id="hkg1",
                desired_generation=1,
                status=ApplyStatus.SUCCEEDED,
                captured_at="bad",
            )

    def test_observed_bgp_protocol_since_optional(self) -> None:
        proto = ObservedBgpProtocol(name="dn42_peer1", state="established")
        assert proto.since is None
        ok = ObservedBgpProtocol(name="x", state="up", since=VALID_TS)
        assert ok.since == VALID_TS
        with pytest.raises(ValidationError):
            ObservedBgpProtocol(name="x", state="up", since="bad")


class TestLinkLocalNeighbor:
    def test_link_local_with_zone_ok(self) -> None:
        BgpSessionSpec(
            name="ll-peer",
            remote_asn=4242420000,
            neighbor="fe80::1%wg0",
            source_address="fe80::2",
            address_family=AddressFamily.IPV6,
        )

    def test_link_local_with_interface_ok(self) -> None:
        BgpSessionSpec(
            name="ll-peer",
            remote_asn=4242420000,
            neighbor="fe80::1",
            source_address="fe80::2",
            address_family=AddressFamily.IPV6,
            interface="wg0",
        )

    def test_link_local_without_zone_or_interface_rejected(self) -> None:
        with pytest.raises(ValidationError, match="link-local"):
            BgpSessionSpec(
                name="ll-peer",
                remote_asn=4242420000,
                neighbor="fe80::1",
                source_address="fe80::2",
                address_family=AddressFamily.IPV6,
            )

    def test_global_ipv6_without_interface_ok(self) -> None:
        BgpSessionSpec(
            name="g-peer",
            remote_asn=4242420000,
            neighbor="fdfc::1",
            source_address="fdfc::2",
            address_family=AddressFamily.IPV6,
        )

    def test_zone_interface_mismatch_still_rejected(self) -> None:
        with pytest.raises(ValidationError, match="zone and interface must match"):
            BgpSessionSpec(
                name="ll-peer",
                remote_asn=4242420000,
                neighbor="fe80::1%wg0",
                source_address="fe80::2",
                address_family=AddressFamily.IPV6,
                interface="wg1",
            )
