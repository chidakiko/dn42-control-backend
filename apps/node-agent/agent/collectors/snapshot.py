from __future__ import annotations

"""把各 collector 输出聚合成 RuntimeSnapshot。"""

from dn42_schemas import DesiredState, ObservationStatus, RuntimeSnapshot

from ..core.clock import utc_now_iso
from .docker import DockerObserver, ObservedProject
from .network import BgpObserver, WireGuardObserver


def build_runtime_snapshot(
    state: DesiredState,
    *,
    applied_generation: int | None = None,
    docker_observer: DockerObserver | None = None,
    wireguard_observer: WireGuardObserver | None = None,
    bgp_observer: BgpObserver | None = None,
) -> RuntimeSnapshot:
    """采集当前节点 runtime 视图。

    容器维度始终采集。WireGuard / BGP 维度只有在显式注入对应 observer 时才填充——
    这些观察需要进入路由 netns 才能采集。未注入对应 observer 时该维度标记
    ``NOT_OBSERVED``（不参与对账）；注入了但容器内命令失败时标记 ``UNAVAILABLE``
    （状态未知，对账产出可见告警，绝不当健康）；命令成功则 ``OBSERVED``，结果
    权威（空也代表真的没有）。

    `applied_generation` 由调用方（orchestrator）传入：容器身份是内容寻址的
    （config_hash），配置未变化时跨多代不重建，因此 generation 不能从容器
    label 推导，只能由 agent 自己声明"我已应用到哪一代"。
    """

    observer = docker_observer or DockerObserver()
    observed: ObservedProject = observer.observe_project(state)

    wg_status, wireguard_interfaces = _observe_wireguard(wireguard_observer)
    bgp_status, bgp_protocols = _observe_bgp(bgp_observer)

    return RuntimeSnapshot(
        node_id=state.node.node_id,
        generation=applied_generation,
        captured_at=utc_now_iso(),
        containers=observed.containers,
        wireguard_interfaces=wireguard_interfaces,
        bgp_protocols=bgp_protocols,
        wireguard_observation=wg_status,
        bgp_observation=bgp_status,
    )


def _observe_wireguard(observer: WireGuardObserver | None):
    """采集 WireGuard 维度，返回 (采集状态, 接口列表)。"""

    if observer is None:
        return ObservationStatus.NOT_OBSERVED, []
    result = observer.observe()
    if result is None:
        return ObservationStatus.UNAVAILABLE, []
    return ObservationStatus.OBSERVED, result


def _observe_bgp(observer: BgpObserver | None):
    """采集 BGP 维度，返回 (采集状态, protocol 列表)。"""

    if observer is None:
        return ObservationStatus.NOT_OBSERVED, []
    result = observer.observe()
    if result is None:
        return ObservationStatus.UNAVAILABLE, []
    return ObservationStatus.OBSERVED, result


__all__ = ["build_runtime_snapshot"]
