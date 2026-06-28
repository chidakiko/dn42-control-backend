from __future__ import annotations

"""FastAPI 依赖注入：从 `app.state` 拿核心组件。"""

import hashlib
import hmac
import logging

from fastapi import Depends, Header, HTTPException, Request, WebSocket, status

from ..core.config import ControlServerConfig
from ..core.events import EventBus
from ..db.engine import Database
from ..services.audit import AuditLogStore
from ..services.desired_state import DesiredStateStore
from ..services.enrollment import EnrollmentTokenStore
from ..services.node_status import NodeStatusStore
from ..services.pending_registrations import PendingRegistrationStore
from ..services.routing import RoutingStore
from ..services.tokens import TokenPrincipal, TokenStore
from ..services.traffic import TrafficStore

_AUTH_SCHEME = "Bearer "

_LOGGER = logging.getLogger("dn42.control.auth")


def get_config(request: Request) -> ControlServerConfig:
    return request.app.state.config


def get_tokens(request: Request) -> TokenStore:
    return request.app.state.tokens


def get_desired_state(request: Request) -> DesiredStateStore:
    return request.app.state.desired_state


def get_node_status(request: Request) -> NodeStatusStore:
    return request.app.state.node_status


def get_routing(request: Request) -> RoutingStore:
    return request.app.state.routing


def get_traffic(request: Request) -> TrafficStore:
    return request.app.state.traffic


def get_pending_registrations(request: Request) -> PendingRegistrationStore:
    return request.app.state.pending_registrations


def get_enrollment_tokens(request: Request) -> EnrollmentTokenStore:
    return request.app.state.enrollment_tokens


def get_audit(request: Request) -> AuditLogStore:
    return request.app.state.audit


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.bus


def get_database(request: Request) -> Database:
    return request.app.state.database


def _ws_app(websocket: WebSocket) -> Request:
    return websocket  # type: ignore[return-value]


def get_tokens_ws(websocket: WebSocket) -> TokenStore:
    return websocket.app.state.tokens


def get_desired_state_ws(websocket: WebSocket) -> DesiredStateStore:
    return websocket.app.state.desired_state


def get_event_bus_ws(websocket: WebSocket) -> EventBus:
    return websocket.app.state.bus


def _parse_bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith(_AUTH_SCHEME):
        return None
    candidate = authorization.removeprefix(_AUTH_SCHEME).strip()
    return candidate or None


async def require_agent(
    authorization: str | None = Header(default=None),
    tokens: TokenStore = Depends(get_tokens),
) -> TokenPrincipal:
    """HTTP 端 Bearer token 解析。校验失败一律返回 401。"""

    token = _parse_bearer(authorization)
    if token is None:
        _LOGGER.warning("agent auth rejected: missing or malformed Authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    principal = await tokens.resolve(token)
    if principal is None:
        # 不记 token 值：只记发生了无效 token 尝试，供探测/吊销后排查。
        _LOGGER.warning("agent auth rejected: token did not resolve to a known principal")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid agent token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


async def require_admin(
    request: Request,
    authorization: str | None = Header(default=None),
    config: ControlServerConfig = Depends(get_config),
) -> str:
    """Admin API Bearer 鉴权。未配置 admin token 时整体 fail-closed。

    返回 actor 标识（当前单 token 模型下固定为 ``admin``），供审计记录使用。
    """

    if config.admin_token is None:
        _LOGGER.error(
            "admin request to %s rejected: DN42_CONTROL_ADMIN_TOKEN not configured (fail-closed)",
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="admin API disabled: DN42_CONTROL_ADMIN_TOKEN is not configured",
        )
    token = _parse_bearer(authorization)
    if token is None or not hmac.compare_digest(_digest(token), _digest(config.admin_token)):
        _LOGGER.warning(
            "admin auth rejected for %s %s: missing or invalid admin token",
            request.method,
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.admin_actor = "admin"
    return "admin"


def parse_ws_bearer(authorization: str | None) -> str | None:
    """WebSocket 握手路径专用 Bearer 提取（不抛异常）。"""

    return _parse_bearer(authorization)


__all__ = [
    "get_audit",
    "get_config",
    "get_database",
    "get_enrollment_tokens",
    "get_desired_state",
    "get_desired_state_ws",
    "get_event_bus",
    "get_event_bus_ws",
    "get_node_status",
    "get_pending_registrations",
    "get_routing",
    "get_traffic",
    "get_tokens",
    "get_tokens_ws",
    "parse_ws_bearer",
    "require_admin",
    "require_agent",
]
