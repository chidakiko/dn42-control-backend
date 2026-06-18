from __future__ import annotations

"""agent 运行时 observer 的单元测试。

observer 是 agent 从本机采集 “现状” 的唯一路径，采到的 RuntimeSnapshot 会
被推送到控制面供 drift 判断。本文件锁定以下解析逻辑：

* ``WireGuardObserver``：从 ``wg show all dump`` 输出（接口行 5 列、peer 行
  9 列、同接口多 peer 可出现多行）中提取接口 listen_port、peer_count
  与状态；WireGuard 没运行 (no runner) 时返回空集、不抛错。
* ``BgpObserver``：从 ``birdc show protocols`` 输出中仅拿 BGP 行（过滤
  掉 Device / Kernel 等），``state`` 使用 BIRD 原始词汇、``session`` 会
  被 ``name_to_session`` 映射为 schema 侧资产 ID。
"""

from dn42_schemas import ObservationStatus, RuntimeResourceStatus
from dn42_schemas.testing import build_hkg1_example_state

from agent.collectors.network import BgpObserver, WireGuardObserver
from agent.collectors.snapshot import build_runtime_snapshot
from agent.collectors.docker import DockerObserver, ObservedProject


class _FakeDockerObserver(DockerObserver):
    def __init__(self) -> None:
        super().__init__(docker_factory=lambda: None)

    def observe_project(self, state):  # type: ignore[override]
        return ObservedProject(project_name="proj")


_WG_DUMP = "\n".join(
    [
        # 接口行：interface privkey pubkey listen-port fwmark
        "\t".join(["as4242420001", "PRIV=", "PUB=", "21841", "off"]),
        # 两个 peer 行（9 列）
        "\t".join(
            ["as4242420001", "PEER1=", "(none)", "1.2.3.4:51820", "0.0.0.0/0", "0", "0", "0", "off"]
        ),
        "\t".join(
            ["as4242420001", "PEER2=", "(none)", "5.6.7.8:51820", "::/0", "0", "0", "0", "off"]
        ),
        # 第二个接口，0 个 peer
        "\t".join(["igp-edge2", "PRIV2=", "PUB2=", "0", "off"]),
    ]
)


def test_wireguard_observer_parses_listen_port_and_peer_count() -> None:
    observer = WireGuardObserver(command_runner=lambda: _WG_DUMP)

    interfaces = observer.observe()
    by_name = {item.name: item for item in interfaces}

    assert by_name["as4242420001"].listen_port == 21841
    assert by_name["as4242420001"].peer_count == 2
    assert by_name["as4242420001"].status == RuntimeResourceStatus.RUNNING
    assert by_name["igp-edge2"].listen_port is None
    assert by_name["igp-edge2"].peer_count == 0


def test_wireguard_observer_returns_none_without_runner() -> None:
    # 无 runner = 未采集，返回 None（区别于"采集成功但空"的 []）。
    assert WireGuardObserver().observe() is None


_BIRD_PROTOCOLS = "\n".join(
    [
        "Name       Proto      Table      State  Since         Info",
        "device1    Device     ---        up     2023-01-01 12:00:00",
        "demopeer_v4 BGP        ---        up     2023-01-01 12:00:00  Established",
        "demopeer_v6 BGP        ---        start  2023-01-01 12:00:00  Active",
    ]
)


def test_bgp_observer_parses_only_bgp_protocols() -> None:
    observer = BgpObserver(
        command_runner=lambda: _BIRD_PROTOCOLS,
        name_to_session={
            "demopeer_v4": "demopeer_4242420001_ex01_v4",
            "demopeer_v6": "demopeer_4242420001_ex01_v6",
        },
    )

    protocols = observer.observe()
    by_name = {item.name: item for item in protocols}

    assert set(by_name) == {"demopeer_v4", "demopeer_v6"}
    assert by_name["demopeer_v4"].state == "Established"
    assert by_name["demopeer_v4"].session == "demopeer_4242420001_ex01_v4"
    assert by_name["demopeer_v6"].state == "Active"


def test_bgp_observer_falls_back_to_protocol_name_for_session() -> None:
    observer = BgpObserver(command_runner=lambda: _BIRD_PROTOCOLS)

    protocols = observer.observe()
    assert protocols[0].session == "demopeer_v4"


def test_bgp_observer_returns_none_without_runner() -> None:
    # 无 runner = 未采集，返回 None（区别于"采集成功但空"的 []）。
    assert BgpObserver().observe() is None


def test_observers_return_none_when_runner_reports_failure() -> None:
    # runner 返回 None（采集失败）时，observe() 透传 None，不退化成 []。
    assert WireGuardObserver(command_runner=lambda: None).observe() is None
    assert BgpObserver(command_runner=lambda: None).observe() is None


def test_observers_return_empty_list_on_successful_empty_output() -> None:
    # 命令成功但无内容：返回 []（"真的没有"），而非 None。
    assert WireGuardObserver(command_runner=lambda: "").observe() == []
    assert BgpObserver(command_runner=lambda: "").observe() == []


def test_build_runtime_snapshot_populates_wireguard_and_bgp_when_observers_injected() -> None:
    state = build_hkg1_example_state()

    snapshot = build_runtime_snapshot(
        state,
        docker_observer=_FakeDockerObserver(),
        wireguard_observer=WireGuardObserver(command_runner=lambda: _WG_DUMP),
        bgp_observer=BgpObserver(command_runner=lambda: _BIRD_PROTOCOLS),
    )

    assert len(snapshot.wireguard_interfaces) == 2
    assert len(snapshot.bgp_protocols) == 2
    assert snapshot.wireguard_observation == ObservationStatus.OBSERVED
    assert snapshot.bgp_observation == ObservationStatus.OBSERVED


def test_build_runtime_snapshot_leaves_wireguard_and_bgp_empty_by_default() -> None:
    state = build_hkg1_example_state()

    snapshot = build_runtime_snapshot(state, docker_observer=_FakeDockerObserver())

    assert snapshot.wireguard_interfaces == []
    assert snapshot.bgp_protocols == []
    assert snapshot.wireguard_observation == ObservationStatus.NOT_OBSERVED
    assert snapshot.bgp_observation == ObservationStatus.NOT_OBSERVED


def test_build_runtime_snapshot_marks_unavailable_when_collection_fails() -> None:
    # 注入了 observer 但容器内命令失败（runner 返回 None）→ UNAVAILABLE，
    # 而不是静默当成"无数据"。
    state = build_hkg1_example_state()

    snapshot = build_runtime_snapshot(
        state,
        docker_observer=_FakeDockerObserver(),
        wireguard_observer=WireGuardObserver(command_runner=lambda: None),
        bgp_observer=BgpObserver(command_runner=lambda: None),
    )

    assert snapshot.wireguard_observation == ObservationStatus.UNAVAILABLE
    assert snapshot.bgp_observation == ObservationStatus.UNAVAILABLE
    assert snapshot.wireguard_interfaces == []
    assert snapshot.bgp_protocols == []
