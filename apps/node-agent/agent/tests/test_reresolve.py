from __future__ import annotations

"""WG endpoint 周期重解析（自愈）的单元测试。

覆盖：目标筛选（只取域名 endpoint）、wg 三列输出解析、stale 判定（超时 / 从未握手 /
不在接口上跳过）、以及端到端编排（stale 域名对端被 ``wg set`` 重设并上报，新鲜则
既不重设也不上报）。全程不碰真实 Docker / 网络。
"""

from types import SimpleNamespace

import pytest

from dn42_schemas import InterfaceKind
from dn42_schemas.testing import build_hkg1_example_state

from agent.core.config import AgentConfig
from agent.core.paths import AgentPaths
from agent.desired_state.cache import save_cached_desired_state
from agent.reresolve import (
    ReresolveTarget,
    _endpoint_host,
    _is_hostname_endpoint,
    collect_reresolve_targets,
    parse_wg_pairs,
    reresolve_and_report,
    select_stale,
)


def _wg_ifaces(state):
    return [i for i in state.interfaces if i.kind == InterfaceKind.WIREGUARD]


def _with_endpoint(state, index: int, endpoint: str | None):
    """返回把第 index 个 WG 接口的 peer endpoint 改成 endpoint 的新 state。"""

    wg = _wg_ifaces(state)
    target_name = wg[index].name
    new_ifaces = []
    for iface in state.interfaces:
        if iface.name == target_name and iface.wireguard_peer is not None:
            peer = iface.wireguard_peer.model_copy(update={"endpoint": endpoint})
            iface = iface.model_copy(update={"wireguard_peer": peer})
        new_ifaces.append(iface)
    return state.model_copy(update={"interfaces": new_ifaces})


# ---- 纯函数 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint,host,is_hostname",
    [
        ("hkg1.exploro.one:32641", "hkg1.exploro.one", True),
        ("47.79.22.184:32641", "47.79.22.184", False),
        ("[2001:db8::1]:51820", "2001:db8::1", False),
        ("bare-host:1", "bare-host", True),
    ],
)
def test_endpoint_host_and_hostname(endpoint, host, is_hostname):
    assert _endpoint_host(endpoint) == host
    assert _is_hostname_endpoint(endpoint) is is_hostname


def test_collect_targets_only_hostname_endpoints():
    state = build_hkg1_example_state()
    state = _with_endpoint(state, 0, "peer.example.dn42:51820")  # 域名 → 入选
    state = _with_endpoint(state, 1, "203.0.113.9:51820")  # 静态 IP → 排除
    targets = collect_reresolve_targets(state)
    names = {t.interface for t in targets}
    assert names == {_wg_ifaces(state)[0].name}
    assert targets[0].endpoint == "peer.example.dn42:51820"


def test_collect_targets_empty_when_no_hostname():
    # 示例态两条 WG 接口 endpoint 均为 None（被动）→ 无目标。
    assert collect_reresolve_targets(build_hkg1_example_state()) == []


def test_parse_wg_pairs_three_columns():
    out = "wg0\tPUBKEYA\t1700000000\nwg1\tPUBKEYB\t(none)\nblank\n"
    parsed = parse_wg_pairs(out)
    assert parsed == {("wg0", "PUBKEYA"): "1700000000", ("wg1", "PUBKEYB"): "(none)"}


def test_select_stale_classifies_each_case():
    targets = [
        ReresolveTarget("wg0", "PUBSTALE", "h.example:1"),
        ReresolveTarget("wg0", "PUBFRESH", "h.example:1"),
        ReresolveTarget("wg0", "PUBNEVER", "h.example:1"),
        ReresolveTarget("wg0", "PUBABSENT", "h.example:1"),  # 不在接口上
    ]
    handshakes = {
        ("wg0", "PUBSTALE"): "1000",
        ("wg0", "PUBFRESH"): "9950",
        ("wg0", "PUBNEVER"): "0",
        # PUBABSENT 故意缺失
    }
    stale = select_stale(targets, handshakes, now=10_000, threshold=135)
    got = {t.public_key: age for t, age in stale}
    assert got == {"PUBSTALE": 9000, "PUBNEVER": None}  # FRESH(50s) 与 ABSENT 不入选


# ---- 端到端编排 ------------------------------------------------------------


class _FakeExec:
    """假 ContainerExec：按 argv 回放 wg 输出并记录 ``wg set`` 调用。"""

    def __init__(self, handshakes: str, endpoints_seq: list[str]):
        self._handshakes = handshakes
        self._endpoints_seq = endpoints_seq
        self._ep_idx = 0
        self.set_calls: list[list[str]] = []

    def run(self, container: str, argv: list[str]):
        if argv[:3] == ["wg", "show", "all"]:
            if argv[3] == "latest-handshakes":
                return (0, self._handshakes, "")
            if argv[3] == "endpoints":
                idx = min(self._ep_idx, len(self._endpoints_seq) - 1)
                self._ep_idx += 1
                return (0, self._endpoints_seq[idx], "")
        if argv[:2] == ["wg", "set"]:
            self.set_calls.append(argv)
            return (0, "", "")
        return (0, "", "")

    def put_file(self, *args, **kwargs) -> None:  # pragma: no cover - 协议补齐
        pass


class _CapturingSession:
    def __init__(self):
        self.posted = []

    def call(self, fn):
        client = SimpleNamespace(post_wireguard_reresolve=self._capture)
        return fn(client)

    def _capture(self, report):
        self.posted.append(report)
        return {"accepted": True}


def _prepare_state(tmp_path, endpoint="peer.example.dn42:51820"):
    state = build_hkg1_example_state()
    state = _with_endpoint(state, 0, endpoint)
    state = _with_endpoint(state, 1, None)  # 第二条保持被动，确保只有一个目标
    node_id = state.node.node_id
    save_cached_desired_state(state, AgentPaths(tmp_path, node_id).desired_state_file)
    wg = _wg_ifaces(state)[0]
    return state, node_id, wg.name, wg.wireguard_peer.public_key


def test_reresolve_resets_stale_and_reports(tmp_path):
    state, node_id, iface, pub = _prepare_state(tmp_path)
    handshakes = f"{iface}\t{pub}\t1000\n"  # 远早于 now → stale
    endpoints_before = f"{iface}\t{pub}\t198.51.100.7:51820\n"
    endpoints_after = f"{iface}\t{pub}\t203.0.113.50:51820\n"  # 重解析后变了
    exec_ = _FakeExec(handshakes, [endpoints_before, endpoints_after])
    session = _CapturingSession()
    adapters = SimpleNamespace(container_exec=exec_, session=session)
    config = AgentConfig(state_dir=tmp_path)

    report = reresolve_and_report(config, adapters, node_id, now=10_000)

    assert report is not None
    assert report.checked == 1
    assert len(report.reresolved) == 1
    entry = report.reresolved[0]
    assert entry.interface == iface
    assert entry.endpoint == "peer.example.dn42:51820"
    assert entry.previous_endpoint == "198.51.100.7:51820"
    assert entry.resolved_endpoint == "203.0.113.50:51820"
    assert entry.stale_seconds == 9000
    # 确有一次 wg set ... endpoint 重设，目标接口 + 公钥正确。
    assert exec_.set_calls == [
        ["wg", "set", iface, "peer", pub, "endpoint", "peer.example.dn42:51820"]
    ]
    # 确有上报。
    assert len(session.posted) == 1
    assert session.posted[0].node_id == node_id


def test_reresolve_noop_when_handshake_fresh(tmp_path):
    state, node_id, iface, pub = _prepare_state(tmp_path)
    handshakes = f"{iface}\t{pub}\t9950\n"  # now-50s，新鲜
    exec_ = _FakeExec(handshakes, ["", ""])
    session = _CapturingSession()
    adapters = SimpleNamespace(container_exec=exec_, session=session)
    config = AgentConfig(state_dir=tmp_path)

    report = reresolve_and_report(config, adapters, node_id, now=10_000)

    assert report is None
    assert exec_.set_calls == []  # 不重设
    assert session.posted == []  # 不上报


def test_reresolve_skips_without_cached_state(tmp_path):
    # 无缓存 desired-state（从未成功 reconcile）→ 安全跳过。
    adapters = SimpleNamespace(container_exec=_FakeExec("", [""]), session=_CapturingSession())
    config = AgentConfig(state_dir=tmp_path)
    assert reresolve_and_report(config, adapters, "edge1", now=10_000) is None
