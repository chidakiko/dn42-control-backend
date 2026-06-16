from __future__ import annotations

"""Agent 与控制面的会话：身份注册、token 生命周期、401 自愈。

身份的显式状态机（持久化载体是节点目录下的 ``identity.json``）：

```
Unregistered ──register──► PendingApproval ──(兜底周期重试)──► Active
                                                        │
              ◄──── invalidate：清空 token、重新注册 ◄──── Invalid(401)
```

关键性质：

- **401 自愈**：token 被轮换/撤销/过期后，agent 不再砖死在无限 401 循环里；
  `call()` 捕获 401 → 作废本地 token → 凭 enrollment token 重新注册 → 重试
  一次。重新注册仍要过 enrollment 校验 + 控制面审批闸门，不构成提权。
- **注册只发生在这里**：reconcile 管线不再内嵌注册逻辑，身份问题与收敛
  问题解耦。
- 等待审批（`BootstrapPendingError`）原样上抛，由常驻循环按兜底周期重试。
"""

from collections.abc import Callable
from typing import Any, TypeVar

from dn42_schemas import HostInventory

from .client.controller import ControllerClient
from .collectors.inventory import build_host_inventory
from .core.config import AgentConfig
from .core.errors import ControllerError
from .core.identity import LocalAgentIdentity, load_identity, save_identity
from .core.logging import get_logger
from .core.paths import AgentPaths

_LOGGER = get_logger("session")

T = TypeVar("T")

InventoryBuilder = Callable[..., HostInventory]


class Session:
    """封装"以哪个身份、用哪个 token 和控制面说话"。"""

    def __init__(
        self,
        config: AgentConfig,
        controller: ControllerClient,
        *,
        inventory_builder: InventoryBuilder = build_host_inventory,
    ) -> None:
        self._config = config
        self._controller = controller
        self._inventory_builder = inventory_builder
        self._identity: LocalAgentIdentity | None = None
        self._registration_ack: dict[str, Any] | None = None

    # ---- 身份生命周期 ----

    def ensure(self) -> LocalAgentIdentity:
        """返回可用身份；本地无 token 时执行注册。

        Raises:
            BootstrapPendingError / BootstrapRejectedError: 注册被挂起/拒绝。
            ControllerError: 无法确定节点身份或注册请求失败。
        """

        identity = self._load()
        if identity.agent_token is not None:
            return identity
        return self._register()

    def invalidate(self, reason: str) -> None:
        """作废本地 token（持久化），下次 `ensure` 将重新注册。"""

        identity = self._load()
        if identity.agent_token is None:
            return
        _LOGGER.warning("session: 作废本地 token（%s），将重新注册", reason)
        identity.agent_token = None
        save_identity(identity, self._paths(self._require_node_id()).identity_file)
        self._identity = identity

    def persist(self, identity: LocalAgentIdentity) -> None:
        """持久化 reconcile 更新后的身份（applied_generation 等）。"""

        assert identity.node_id is not None
        save_identity(identity, self._paths(identity.node_id).identity_file)
        self._identity = identity

    def take_registration_ack(self) -> dict[str, Any] | None:
        """取走本次会话中最近一次注册响应（用于结果摘要），取后清空。"""

        ack, self._registration_ack = self._registration_ack, None
        return ack

    # ---- 鉴权调用 ----

    def call(self, fn: Callable[[ControllerClient], T]) -> T:
        """以当前身份执行一次控制面调用；401 时自愈（重注册）后重试一次。"""

        identity = self.ensure()
        assert identity.agent_token is not None
        try:
            with self._controller.with_token(identity.agent_token):
                return fn(self._controller)
        except ControllerError as exc:
            if exc.status_code != 401:
                raise
            self.invalidate(f"controller rejected token (401): {exc}")
            identity = self.ensure()
            assert identity.agent_token is not None
            with self._controller.with_token(identity.agent_token):
                return fn(self._controller)

    # ---- 内部 ----

    def _load(self) -> LocalAgentIdentity:
        if self._identity is None:
            self._identity = load_identity(
                self._paths(self._require_node_id()).identity_file
            )
        return self._identity

    def _register(self) -> LocalAgentIdentity:
        node_id = self._require_node_id()
        inventory = self._inventory_builder(hostname=self._config.hostname)
        registration = self._controller.register(
            enrollment_token=self._config.enrollment_token or "",
            inventory=inventory,
            requested_node_id=node_id,
        )
        self._registration_ack = registration.model_dump(mode="json")
        identity = LocalAgentIdentity(
            node_id=registration.node_id,
            agent_id=registration.agent_id,
            agent_token=registration.agent_token,
        )
        assert identity.node_id is not None
        save_identity(identity, self._paths(identity.node_id).identity_file)
        self._identity = identity
        _LOGGER.info("session: 注册成功，node=%s", registration.node_id)
        return identity

    def _require_node_id(self) -> str:
        node_id = self._config.requested_node_id or self._detect_node_id()
        if node_id is None:
            raise ControllerError(
                "无法确定节点身份：请配置 requested_node_id"
                "（--requested-node-id / DN42_AGENT_REQUESTED_NODE_ID）"
            )
        return node_id

    def _detect_node_id(self) -> str | None:
        """未显式配置时，从 state_dir 下唯一的节点目录推断身份。"""

        nodes_dir = self._config.state_dir / "nodes"
        if not nodes_dir.is_dir():
            return None
        candidates = [p.name for p in nodes_dir.iterdir() if p.is_dir()]
        return candidates[0] if len(candidates) == 1 else None

    def _paths(self, node_id: str) -> AgentPaths:
        return AgentPaths(self._config.state_dir, node_id)


__all__ = ["InventoryBuilder", "Session"]
