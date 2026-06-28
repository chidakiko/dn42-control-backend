from __future__ import annotations

"""封装 Control Server Agent API 调用。

设计目标：
- 完整覆盖 docs/api.md 中 Agent API 的端点。
- 输入输出全部使用 `dn42_schemas` 模型，避免 dict 漂移。
- 通过依赖注入的 `httpx.Client` 让单测可以接 `MockTransport`。
"""

from contextlib import contextmanager
from typing import Iterator

import httpx

from dn42_schemas import (
    AgentRegistrationRequest,
    AgentRegistrationResponse,
    ApplyResult,
    BootstrapStatus,
    DesiredState,
    HostInventory,
    RecoveryPublicKeyResponse,
    ReconciliationReport,
    RoutingTableSnapshot,
    RuntimeSnapshot,
    WireGuardKeyReport,
    WireGuardKeyReportResult,
    WireGuardReresolveReport,
    WireGuardTrafficSample,
)

from ..core.errors import (
    BootstrapPendingError,
    BootstrapRejectedError,
    ControllerError,
)


class ControllerClient:
    """Control Server Agent API 的强类型客户端。

    - 由调用方负责 `httpx.Client` 的生命周期；本类不会代为关闭。
    - 通过 `with_token` 进入注册成功后的鉴权上下文。
    """

    def __init__(self, http: httpx.Client) -> None:
        self._http = http
        self._token: str | None = None

    @classmethod
    def for_url(cls, base_url: str, *, timeout: float = 10.0) -> "ControllerClient":
        """便捷构造：自带 `httpx.Client`，调用方需要负责调用 `close()`。"""

        client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        return cls(client)

    def close(self) -> None:
        """关闭底层 httpx 连接池。"""

        self._http.close()

    @contextmanager
    def with_token(self, token: str) -> Iterator["ControllerClient"]:
        """在 `with` 块内临时使用指定的 agent token 进行鉴权调用。"""

        previous = self._token
        self._token = token
        try:
            yield self
        finally:
            self._token = previous

    def register(
        self,
        *,
        enrollment_token: str,
        inventory: HostInventory,
        requested_node_id: str,
    ) -> AgentRegistrationResponse:
        """空节点注册流程。

        - `accepted` 状态会附带新 token 与世代号；
        - `pending-approval` 抛出 `BootstrapPendingError`；
        - `rejected` 抛出 `BootstrapRejectedError`。
        """

        request = AgentRegistrationRequest(
            enrollment_token=enrollment_token,
            requested_node_id=requested_node_id,
            inventory=inventory,
        )
        response = self._http.post(
            "/api/v1/agent/register",
            json=request.model_dump(mode="json"),
        )
        self._raise_for_status(response, "register")
        result = AgentRegistrationResponse.model_validate(response.json())
        if result.status == BootstrapStatus.PENDING_APPROVAL:
            raise BootstrapPendingError(result.message or "registration is pending approval")
        if result.status == BootstrapStatus.REJECTED:
            raise BootstrapRejectedError(result.message or "registration was rejected")
        return result

    def fetch_desired_state(self) -> DesiredState:
        """拉取当前节点的 Desired State。"""

        response = self._http.get(
            "/api/v1/agent/desired-state",
            headers=self._auth_headers(),
        )
        self._raise_for_status(response, "fetch_desired_state")
        return DesiredState.model_validate(response.json())

    def fetch_recovery_public_key(self) -> RecoveryPublicKeyResponse:
        """拉取离线托管的恢复公钥（用于封装本端 WG 私钥）。"""

        response = self._http.get(
            "/api/v1/agent/recovery-public-key",
            headers=self._auth_headers(),
        )
        self._raise_for_status(response, "fetch_recovery_public_key")
        return RecoveryPublicKeyResponse.model_validate(response.json())

    def report_wireguard_keys(self, report: WireGuardKeyReport) -> WireGuardKeyReportResult:
        """上报本端 WG 公钥 + 托管密文，触发控制面一致性校验。

        公钥与控制面记录冲突时控制面回 409，``_raise_for_status`` 抛
        ``ControllerError(status_code=409)``，由上层中止 apply。
        """

        response = self._http.post(
            "/api/v1/agent/wireguard-keys",
            headers=self._auth_headers(),
            json=report.model_dump(mode="json"),
        )
        self._raise_for_status(response, "report_wireguard_keys")
        return WireGuardKeyReportResult.model_validate(response.json())

    def post_runtime_snapshot(self, snapshot: RuntimeSnapshot) -> dict[str, object]:
        """上报最新观察到的 runtime 状态。"""

        response = self._http.post(
            "/api/v1/agent/runtime-snapshot",
            headers=self._json_headers(),
            content=snapshot.model_dump_json(),
        )
        self._raise_for_status(response, "post_runtime_snapshot")
        return response.json()

    def post_routing_table(self, snapshot: RoutingTableSnapshot) -> dict[str, object]:
        """上报最新观测到的 BIRD 路由全表（独立于 reconcile 的周期观测）。"""

        response = self._http.post(
            "/api/v1/agent/routing-table",
            headers=self._json_headers(),
            content=snapshot.model_dump_json(),
        )
        self._raise_for_status(response, "post_routing_table")
        return response.json()

    def post_wireguard_traffic(self, sample: WireGuardTrafficSample) -> dict[str, object]:
        """上报 30s 轻量 WG 流量采样（全 peer 累计收 / 发字节之和，独立于 reconcile）。

        旧控制面无此端点会回 404，调用方按 best-effort 吞掉——采集本身已无副作用。
        """

        response = self._http.post(
            "/api/v1/agent/wireguard-traffic",
            headers=self._auth_headers(),
            json=sample.model_dump(mode="json"),
        )
        self._raise_for_status(response, "post_wireguard_traffic")
        return response.json()

    def post_wireguard_reresolve(self, report: WireGuardReresolveReport) -> dict[str, object]:
        """上报本轮 WG endpoint 重解析结果（独立于 reconcile 的自愈观测）。

        旧控制面无此端点会回 404，调用方按 best-effort 吞掉——自愈本身已生效。
        """

        response = self._http.post(
            "/api/v1/agent/wireguard-reresolve",
            headers=self._auth_headers(),
            json=report.model_dump(mode="json"),
        )
        self._raise_for_status(response, "post_wireguard_reresolve")
        return response.json()

    def post_reconciliation_report(self, report: ReconciliationReport) -> dict[str, object]:
        """上报 desired vs observed 的对账结果。"""

        response = self._http.post(
            "/api/v1/agent/reconciliation-report",
            headers=self._auth_headers(),
            json=report.model_dump(mode="json"),
        )
        self._raise_for_status(response, "post_reconciliation_report")
        return response.json()

    def post_apply_result(self, result: ApplyResult) -> dict[str, object]:
        """上报一次 reconcile 尝试的结果。"""

        response = self._http.post(
            "/api/v1/agent/apply-result",
            headers=self._auth_headers(),
            json=result.model_dump(mode="json"),
        )
        self._raise_for_status(response, "post_apply_result")
        return response.json()

    def _auth_headers(self) -> dict[str, str]:
        if self._token is None:
            return {}
        return {"Authorization": f"Bearer {self._token}"}

    def _json_headers(self) -> dict[str, str]:
        """鉴权头 + 显式 Content-Type，配合 ``content=model_dump_json()`` 直发 bytes。

        大对象（runtime-snapshot / routing-table）走 ``model_dump_json()`` 让 pydantic
        在 Rust 端一步序列化成 JSON bytes，省掉 ``model_dump(mode="json")`` 那遍中间
        dict 的构造与 httpx 再编码；用 ``content=`` 需自带 Content-Type。
        """

        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _raise_for_status(response: httpx.Response, action: str) -> None:
        if response.is_success:
            return
        raise ControllerError(
            f"controller {action} failed with HTTP {response.status_code}: {response.text}",
            status_code=response.status_code,
        )


__all__ = ["ControllerClient"]
