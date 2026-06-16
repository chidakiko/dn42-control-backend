from __future__ import annotations

"""agent ↔ control-server JSON 协议的 Pydantic 记录层校验测试。

该套用例以 ``dn42_schemas`` 导出的三个关键 schema 为中心，锐意锁定
“控制面与节点 agent 交换报文” 的序列化与接口语义：

* ``AgentRegistrationRequest``：验证 ``requested_node_id`` 必填，
  ``inventory.capabilities`` 会被原样保留（例如 docker-compose / bird /
  wireguard 能力集）。
* ``AgentRegistrationResponse``：``status=accepted`` 必须同时提供
  ``agent_id`` / ``agent_token`` / ``desired_state_generation``，缺一则抛
  ``ValidationError``，防止控制面返回不完整凭证。
* ``RuntimeSnapshot``：包含路由容器 / 接口 / WireGuard / BGP 状态；
  ``ObservedInterface.addresses`` 需要是合法 IP，负面例验证 “not-an
  -address” 被拒绝。
* ``ReconciliationReport``：漂移项 ``DriftItem`` 包括严重级别、
  desired vs observed 对比。
"""

import pytest
from pydantic import ValidationError

from dn42_schemas import (
    AgentCapability,
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    ApplyStatus,
    BootstrapStatus,
    DriftItem,
    DriftSeverity,
    HostInventory,
    ObservedContainer,
    ObservedInterface,
    ReconciliationReport,
    RuntimeResourceStatus,
    RuntimeSnapshot,
    ServiceRole,
)


def build_inventory() -> HostInventory:
    return HostInventory(
        hostname="edge1",
        os="linux",
        arch="amd64",
        kernel="6.8.0",
        container_runtime="docker",
        container_runtime_version="26.1.0",
        has_systemd=True,
        capabilities=[
            AgentCapability.DOCKER,
            AgentCapability.WIREGUARD,
            AgentCapability.BIRD,
        ],
    )


def test_agent_registration_request_requires_node_id() -> None:
    """节点身份必须显式声明：缺 requested_node_id 直接校验失败。"""

    with pytest.raises(ValidationError):
        AgentRegistrationRequest(  # pyright: ignore[reportCallIssue]  # 故意缺 requested_node_id
            enrollment_token="enroll-token",
            inventory=build_inventory(),
        )

    request = AgentRegistrationRequest(
        enrollment_token="enroll-token",
        requested_node_id="edge1",
        inventory=build_inventory(),
    )
    assert request.requested_node_id == "edge1"
    assert AgentCapability.DOCKER in request.inventory.capabilities


def test_accepted_registration_requires_credentials_and_generation() -> None:
    with pytest.raises(ValidationError, match="accepted registration is missing"):
        AgentRegistrationResponse(status=BootstrapStatus.ACCEPTED, node_id="edge1")

    response = AgentRegistrationResponse(
        status=BootstrapStatus.ACCEPTED,
        node_id="edge1",
        agent_id="agent-01",
        agent_token="node-token",
        desired_state_generation=1,
    )

    assert response.status == BootstrapStatus.ACCEPTED


def test_runtime_snapshot_validates_observed_interface_addresses() -> None:
    snapshot = RuntimeSnapshot(
        node_id="edge1",
        generation=1,
        captured_at="2026-05-14T02:00:00Z",
        containers=[
            ObservedContainer(
                name="dn42-router-netns",
                role=ServiceRole.ROUTER_NETNS,
                config_hash="0123456789abcdef",
                status=RuntimeResourceStatus.RUNNING,
                healthy=True,
            )
        ],
        interfaces=[
            ObservedInterface(
                name="dn42-lo",
                addresses=["172.20.0.62/32"],
                status=RuntimeResourceStatus.RUNNING,
            )
        ],
    )

    assert snapshot.containers[0].role == ServiceRole.ROUTER_NETNS

    with pytest.raises(ValidationError):
        ObservedInterface(name="bad0", addresses=["not-an-address"])


def test_reconciliation_report_carries_drift_items() -> None:
    report = ReconciliationReport(
        node_id="edge1",
        desired_generation=2,
        observed_generation=1,
        status=ApplyStatus.DEGRADED,
        captured_at="2026-05-14T02:00:00Z",
        drift=[
            DriftItem(
                component="container",
                name="dn42-bird-router",
                severity=DriftSeverity.CRITICAL,
                message="container is missing",
                desired="running",
                observed="missing",
            )
        ],
    )

    assert report.drift[0].severity == DriftSeverity.CRITICAL
