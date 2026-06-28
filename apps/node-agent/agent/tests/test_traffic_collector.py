from __future__ import annotations

"""WG 30s 轻量流量采集（旁路观测）的单元测试。

覆盖：``wg show all transfer`` 解析（求和 + peer 计数 + 残缺行跳过 + 计数钳零）、以及
端到端编排（读缓存 desired-state 拿 wg 容器、采集求和、上报 ``WireGuardTrafficSample``）。
全程不碰真实 Docker / 网络。
"""

from types import SimpleNamespace

from dn42_schemas.testing import build_hkg1_example_state

from agent.core.config import AgentConfig
from agent.core.paths import AgentPaths
from agent.desired_state.cache import save_cached_desired_state
from agent.traffic import collect_and_publish_traffic, parse_wg_transfer


# ---- 纯解析 ----------------------------------------------------------------


def test_parse_wg_transfer_sums_and_counts():
    out = "wg0\tPUBA\t1000\t2000\nwg0\tPUBB\t3000\t4000\nwg1\tPUBC\t10\t20\n"
    rx, tx, peers = parse_wg_transfer(out)
    assert (rx, tx, peers) == (4010, 6020, 3)


def test_parse_wg_transfer_skips_malformed_and_clamps_negative():
    # 残缺行（<4 列）/ 非数字跳过；负计数（异常）钳到 0。
    out = "short\tline\nwg0\tPUBA\tnotnum\t5\nwg0\tPUBB\t-7\t9\n"
    rx, tx, peers = parse_wg_transfer(out)
    assert (rx, tx, peers) == (0, 9, 1)  # 仅 PUBB 计入，rx 钳 0


def test_parse_wg_transfer_empty():
    assert parse_wg_transfer("") == (0, 0, 0)


# ---- 端到端编排 ------------------------------------------------------------


class _FakeExec:
    """假 ContainerExec：对 ``wg show all transfer`` 回放固定输出。"""

    def __init__(self, transfer: str, *, rc: int = 0):
        self._transfer = transfer
        self._rc = rc
        self.calls: list[list[str]] = []

    def run(self, container: str, argv: list[str]):
        self.calls.append(argv)
        if argv == ["wg", "show", "all", "transfer"]:
            return (self._rc, self._transfer, "" if self._rc == 0 else "boom")
        return (0, "", "")

    def put_file(self, *args, **kwargs) -> None:  # pragma: no cover - 协议补齐
        pass


class _CapturingSession:
    def __init__(self):
        self.posted = []

    def call(self, fn):
        client = SimpleNamespace(post_wireguard_traffic=self._capture)
        return fn(client)

    def _capture(self, sample):
        self.posted.append(sample)
        return {"accepted": True}


def _prepare(tmp_path) -> str:
    state = build_hkg1_example_state()
    node_id = state.node.node_id
    save_cached_desired_state(state, AgentPaths(tmp_path, node_id).desired_state_file)
    return node_id


def test_collect_and_publish_traffic_reports_sums(tmp_path):
    node_id = _prepare(tmp_path)
    exec_ = _FakeExec("wg0\tPUBA\t1000\t2000\nwg0\tPUBB\t500\t600\n")
    session = _CapturingSession()
    adapters = SimpleNamespace(container_exec=exec_, session=session)

    sample = collect_and_publish_traffic(AgentConfig(state_dir=tmp_path), adapters, node_id)

    assert sample is not None
    assert sample.node_id == node_id
    assert sample.rx_bytes == 1500
    assert sample.tx_bytes == 2600
    assert sample.peer_count == 2
    assert len(session.posted) == 1
    assert session.posted[0].rx_bytes == 1500


def test_collect_skips_without_cached_state(tmp_path):
    # 无缓存 desired-state → 跳过本轮，返回 None，不打控制面。
    session = _CapturingSession()
    adapters = SimpleNamespace(container_exec=_FakeExec(""), session=session)
    assert collect_and_publish_traffic(AgentConfig(state_dir=tmp_path), adapters, "edge1") is None
    assert session.posted == []


def test_collect_returns_none_on_exec_failure(tmp_path):
    node_id = _prepare(tmp_path)
    exec_ = _FakeExec("", rc=1)
    session = _CapturingSession()
    adapters = SimpleNamespace(container_exec=exec_, session=session)
    assert collect_and_publish_traffic(AgentConfig(state_dir=tmp_path), adapters, node_id) is None
    assert session.posted == []  # 采集失败不上报
