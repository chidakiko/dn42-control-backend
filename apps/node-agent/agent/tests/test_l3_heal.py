from __future__ import annotations

"""L3 漂移自愈回路（``agent.l3_heal``）单测。

锁定不变量：期望接口（WG + dummy）在 netns 缺失时,重跑 apply-all-wg 补齐;全部在位时
不动手。用 fake container_exec,不依赖真实 docker / 内核,任意平台可跑。
"""

from dn42_schemas.testing import build_hkg1_example_state

from agent import l3_heal
from agent.l3_heal import HealCircuit
from agent.core.config import AgentConfig


class _FakeExec:
    """记录调用;``ip -br link`` 按预设序列返回存在的接口,``apply-all-wg`` 记一次并成功。"""

    def __init__(self, present_sequence: list[set[str]]) -> None:
        self.calls: list[list[str]] = []
        self._present = present_sequence
        self._i = 0

    def run(self, container: str, cmd: list[str]):  # noqa: ANN001
        self.calls.append(cmd)
        if cmd[:3] == ["ip", "-br", "link"]:
            names = self._present[min(self._i, len(self._present) - 1)]
            self._i += 1
            return 0, "\n".join(f"{n}@if1 UP ether" for n in sorted(names)), ""
        return 0, "", ""

    @property
    def applied(self) -> bool:
        return any(c[:1] == ["sh"] and "apply-all-wg" in c[1] for c in self.calls if len(c) > 1)


class _FakeAdapters:
    def __init__(self, exec_: _FakeExec) -> None:
        self.container_exec = exec_


def test_expected_interfaces_covers_wireguard_and_dummy() -> None:
    state = build_hkg1_example_state()
    expected = l3_heal.expected_interfaces(state)
    assert "dn42-lo" in expected  # dummy（身份）
    assert "dns-anycast" in expected  # dummy（任播）
    assert "as4242420001" in expected  # WireGuard


def test_parse_link_names_strips_parent_suffix() -> None:
    out = "lo             UNKNOWN ...\nwg-hkg1@if5     UP ...\ndn42-lo        UP ..."
    assert l3_heal.parse_link_names(out) == {"lo", "wg-hkg1", "dn42-lo"}


def test_heal_reapplies_when_an_interface_is_missing(monkeypatch) -> None:
    state = build_hkg1_example_state()
    monkeypatch.setattr(l3_heal, "load_cached_desired_state", lambda _p: state)
    expected = l3_heal.expected_interfaces(state)
    gone = sorted(expected)[0]
    missing_once = set(expected) - {gone}
    # 第一次探测缺一个 -> 重 apply;复核时全部在位。
    fexec = _FakeExec([missing_once, expected])

    result = l3_heal.heal_l3_drift(AgentConfig(), _FakeAdapters(fexec), "hkg1-edge")

    assert result is not None
    assert gone in result["missing_interfaces"]
    assert result["healed"] is True
    assert fexec.applied  # apply-all-wg 被调用


def test_heal_is_noop_when_all_interfaces_present(monkeypatch) -> None:
    state = build_hkg1_example_state()
    monkeypatch.setattr(l3_heal, "load_cached_desired_state", lambda _p: state)
    expected = l3_heal.expected_interfaces(state)
    fexec = _FakeExec([expected])

    result = l3_heal.heal_l3_drift(AgentConfig(), _FakeAdapters(fexec), "hkg1-edge")

    assert result is None  # 无漂移
    assert not fexec.applied  # 绝不无故重 apply


def test_heal_skips_when_probe_unavailable(monkeypatch) -> None:
    state = build_hkg1_example_state()
    monkeypatch.setattr(l3_heal, "load_cached_desired_state", lambda _p: state)

    class _Down(_FakeExec):
        def run(self, container, cmd):  # noqa: ANN001
            self.calls.append(cmd)
            if cmd[:3] == ["ip", "-br", "link"]:
                return 1, "", "exec failed"  # 容器不可达
            return 0, "", ""

    fexec = _Down([set()])
    result = l3_heal.heal_l3_drift(AgentConfig(), _FakeAdapters(fexec), "hkg1-edge")

    assert result is None  # 探针不可达 -> 跳过,不误判缺失
    assert not fexec.applied


def test_heal_restarts_bird_when_socket_dead(monkeypatch) -> None:
    state = build_hkg1_example_state()
    monkeypatch.setattr(l3_heal, "load_cached_desired_state", lambda _p: state)
    expected = l3_heal.expected_interfaces(state)

    class _BirdDead(_FakeExec):
        def __init__(self, present_sequence: list[set[str]]) -> None:
            super().__init__(present_sequence)
            self._bird_probes = 0

        def run(self, container, cmd):  # noqa: ANN001
            self.calls.append(cmd)
            if cmd[:3] == ["ip", "-br", "link"]:
                names = self._present[min(self._i, len(self._present) - 1)]
                self._i += 1
                return 0, "\n".join(f"{n}@if1 UP ether" for n in sorted(names)), ""
            if cmd[:3] == ["birdc", "show", "status"]:
                self._bird_probes += 1
                # 首探 bird 死（socket 拒连），重跑 apply-bird 后复核已活。
                return (1, "", "Connection refused") if self._bird_probes == 1 else (0, "ok", "")
            return 0, "", ""

    fexec = _BirdDead([expected])  # 接口齐全，仅 bird 死
    result = l3_heal.heal_l3_drift(AgentConfig(), _FakeAdapters(fexec), "hkg1-edge")

    assert result is not None
    assert result.get("bird_dead") is True
    assert result["healed"] is True
    assert any(len(c) > 1 and c[0] == "sh" and "apply-bird" in c[1] for c in fexec.calls)


def test_circuit_backoff_grows_then_caps() -> None:
    c = HealCircuit(60.0, threshold=3, max_backoff=600.0)
    assert c.backoff() == 60.0  # 无失败 = base
    c.record(False)
    assert c.backoff() == 120.0  # 60 * 2^1
    c.record(False)
    assert c.backoff() == 240.0  # 60 * 2^2
    for _ in range(6):
        c.record(False)
    assert c.backoff() == 600.0  # 指数封顶


def test_circuit_escalates_once_at_threshold() -> None:
    c = HealCircuit(60.0, threshold=3)
    assert c.record(False) is None
    assert c.record(False) is None
    assert c.record(False) == "escalate"  # 第 3 次 -> 熔断告警
    assert c.record(False) is None  # 不重复告警


def test_circuit_resets_and_recovers_after_open() -> None:
    c = HealCircuit(60.0, threshold=3)
    c.record(False)
    c.record(False)
    c.record(False)  # 熔断
    assert c.record(True) == "recovered"  # 熔断后成功 -> 复位告警
    assert c.failures == 0
    assert c.backoff() == 60.0
    assert c.record(True) is None  # 正常成功不报


def test_circuit_silent_reset_before_threshold() -> None:
    c = HealCircuit(60.0, threshold=3)
    c.record(False)
    c.record(False)  # 未到阈值
    assert c.record(True) is None  # 未熔断,复位不报
    assert c.failures == 0
