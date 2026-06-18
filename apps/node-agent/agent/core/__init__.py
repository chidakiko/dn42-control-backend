from __future__ import annotations

"""Agent 基础设施层：配置、身份、命名、路径、时钟、日志、错误。"""

from .clock import utc_now, utc_now_iso
from .config import AgentConfig, load_agent_config
from .errors import (
    AgentError,
    ApplyError,
    BootstrapPendingError,
    BootstrapRejectedError,
    ConfigError,
    ControllerError,
    DesiredStateError,
    RenderError,
)
from .identity import LocalAgentIdentity, load_identity, save_identity
from .logging import configure_logging, get_logger
from .naming import node_project_name, service_container_name
from .paths import AgentPaths


__all__ = [
    "AgentConfig",
    "AgentError",
    "AgentPaths",
    "ApplyError",
    "BootstrapPendingError",
    "BootstrapRejectedError",
    "ConfigError",
    "ControllerError",
    "DesiredStateError",
    "LocalAgentIdentity",
    "RenderError",
    "node_project_name",
    "configure_logging",
    "get_logger",
    "load_agent_config",
    "load_identity",
    "save_identity",
    "service_container_name",
    "utc_now",
    "utc_now_iso",
]
