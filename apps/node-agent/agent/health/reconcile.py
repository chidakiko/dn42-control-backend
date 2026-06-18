from __future__ import annotations

"""Desired State 与 RuntimeSnapshot 之间的对账逻辑。

容器维度始终对账；WireGuard / BGP 维度按 snapshot 的采集状态分三态处理：

- ``NOT_OBSERVED``：没有对应观察器（全新节点 / 无该角色容器），跳过不判；
- ``UNAVAILABLE``：观察器在场但容器内命令失败，状态未知——若期望存在该维度
  资源，产出一条 WARNING（可见、不当健康），但不武断判 CRITICAL；
- ``OBSERVED``：采集成功，结果权威。期望存在而观测缺失即判 CRITICAL，
  不再因"采集为空"被静默跳过。
"""

from typing import Mapping

from dn42_schemas import (
    ApplyStatus,
    DesiredState,
    DriftItem,
    DriftSeverity,
    InterfaceKind,
    ObservationStatus,
    ReconciliationReport,
    RuntimeResourceStatus,
    RuntimeSnapshot,
)

from ..core.clock import utc_now_iso
from ..core.naming import node_project_name, service_container_name


def build_reconciliation_report(
    state: DesiredState,
    snapshot: RuntimeSnapshot,
    *,
    apply_status: ApplyStatus,
    desired_hashes: Mapping[str, str] | None = None,
) -> ReconciliationReport:
    """对账 desired runtime 与 observed snapshot。

    ``desired_hashes``（容器名 -> 期望定义哈希）来自本轮的 ContainerPlan
    （`plan.container_plan.desired_hashes`），保证对账与计划/执行同源；
    缺省时跳过哈希维度，仅对账存在性与运行状态。
    """

    drift = _collect_container_drift(state, snapshot, desired_hashes or {})
    drift.extend(_collect_wireguard_drift(state, snapshot))
    drift.extend(_collect_bgp_drift(state, snapshot))
    final_status = apply_status

    if any(item.severity == DriftSeverity.CRITICAL for item in drift):
        if apply_status in {ApplyStatus.SUCCEEDED, ApplyStatus.SKIPPED}:
            final_status = ApplyStatus.DEGRADED

    return ReconciliationReport(
        node_id=state.node.node_id,
        desired_generation=state.generation,
        observed_generation=snapshot.generation,
        status=final_status,
        captured_at=utc_now_iso(),
        drift=drift,
    )


def _collect_container_drift(
    state: DesiredState,
    snapshot: RuntimeSnapshot,
    desired_hashes: Mapping[str, str],
) -> list[DriftItem]:
    project = node_project_name(state)
    observed_by_name = {container.name: container for container in snapshot.containers}
    drift: list[DriftItem] = []

    expected_names: set[str] = set()
    for service in state.runtime.services:
        if not service.enabled:
            continue
        container_name = service_container_name(project, service.name)
        expected_names.add(container_name)
        observed = observed_by_name.get(container_name)
        if observed is None:
            drift.append(
                DriftItem(
                    component="container",
                    name=container_name,
                    severity=DriftSeverity.CRITICAL,
                    message="container is missing",
                    desired="running",
                    observed="missing",
                )
            )
            continue

        if observed.status != RuntimeResourceStatus.RUNNING:
            drift.append(
                DriftItem(
                    component="container",
                    name=container_name,
                    severity=DriftSeverity.CRITICAL,
                    message=f"container is not running (observed={observed.status.value})",
                    desired="running",
                    observed=observed.status.value,
                )
            )
            continue

        desired_hash = desired_hashes.get(container_name)
        if desired_hash is not None and observed.config_hash != desired_hash:
            drift.append(
                DriftItem(
                    component="container",
                    name=container_name,
                    severity=DriftSeverity.WARNING,
                    message="container config hash differs from desired",
                    desired=desired_hash,
                    observed=observed.config_hash or "missing",
                )
            )

    for observed in snapshot.containers:
        if observed.name in expected_names:
            continue
        drift.append(
            DriftItem(
                component="container",
                name=observed.name,
                severity=DriftSeverity.WARNING,
                message="unmanaged container carries dn42 labels",
                desired="absent",
                observed=observed.status.value,
            )
        )

    return drift


def _expected_wireguard_interfaces(state: DesiredState) -> list:
    return [iface for iface in state.interfaces if iface.kind == InterfaceKind.WIREGUARD]


def _collect_wireguard_drift(state: DesiredState, snapshot: RuntimeSnapshot) -> list[DriftItem]:
    """对账期望的 WireGuard 接口与观测结果。

    采集失败（UNAVAILABLE）且期望存在 WG 接口时产出一条 WARNING（状态未知，
    不当健康）；采集成功（OBSERVED）才逐接口判定：缺失记 CRITICAL，`listen_port`
    不符或 `peer_count == 0` 记 WARNING；未采集（NOT_OBSERVED）跳过。
    """

    expected = _expected_wireguard_interfaces(state)

    if snapshot.wireguard_observation == ObservationStatus.NOT_OBSERVED:
        return []
    if snapshot.wireguard_observation == ObservationStatus.UNAVAILABLE:
        if not expected:
            return []
        return [
            DriftItem(
                component="wireguard",
                name="*",
                severity=DriftSeverity.WARNING,
                message="wireguard 状态无法采集（容器内命令失败），健康未确认",
                desired="observed",
                observed="unavailable",
            )
        ]

    observed_by_name = {item.name: item for item in snapshot.wireguard_interfaces}
    drift: list[DriftItem] = []
    for interface in expected:
        observed = observed_by_name.get(interface.name)
        if observed is None:
            drift.append(
                DriftItem(
                    component="wireguard",
                    name=interface.name,
                    severity=DriftSeverity.CRITICAL,
                    message="wireguard interface is missing",
                    desired="present",
                    observed="missing",
                )
            )
            continue

        if interface.listen_port is not None and observed.listen_port != interface.listen_port:
            drift.append(
                DriftItem(
                    component="wireguard",
                    name=interface.name,
                    severity=DriftSeverity.WARNING,
                    message="wireguard listen port differs from desired",
                    desired=str(interface.listen_port),
                    observed=str(observed.listen_port),
                )
            )

        if observed.peer_count == 0:
            drift.append(
                DriftItem(
                    component="wireguard",
                    name=interface.name,
                    severity=DriftSeverity.WARNING,
                    message="wireguard interface has no active peers",
                    desired=">=1",
                    observed="0",
                )
            )

    return drift


def _collect_bgp_drift(state: DesiredState, snapshot: RuntimeSnapshot) -> list[DriftItem]:
    """对账期望的 BGP 会话与 BIRD 观测到的 protocol 状态。

    采集失败（UNAVAILABLE）且存在 enabled 会话时产出一条 WARNING（状态未知）；
    采集成功（OBSERVED）才逐会话判定：protocol 非 `Established` 记 WARNING；
    未采集（NOT_OBSERVED）跳过。反查不到的 protocol 不判，留给容器维度兜底。
    """

    enabled_sessions = [session for session in state.bgp_sessions if session.enabled]

    if snapshot.bgp_observation == ObservationStatus.NOT_OBSERVED:
        return []
    if snapshot.bgp_observation == ObservationStatus.UNAVAILABLE:
        if not enabled_sessions:
            return []
        return [
            DriftItem(
                component="bgp",
                name="*",
                severity=DriftSeverity.WARNING,
                message="bgp 状态无法采集（容器内命令失败），健康未确认",
                desired="observed",
                observed="unavailable",
            )
        ]

    observed_by_session = {
        protocol.session: protocol
        for protocol in snapshot.bgp_protocols
        if protocol.session is not None
    }
    drift: list[DriftItem] = []
    for session in enabled_sessions:
        observed = observed_by_session.get(session.name)
        if observed is None:
            continue
        if observed.state.strip().lower() != "established":
            drift.append(
                DriftItem(
                    component="bgp",
                    name=session.name,
                    severity=DriftSeverity.WARNING,
                    message="bgp session is not established",
                    desired="Established",
                    observed=observed.state,
                )
            )

    return drift


__all__ = ["build_reconciliation_report"]
