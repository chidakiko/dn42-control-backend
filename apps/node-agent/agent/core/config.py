from __future__ import annotations

"""Agent 运行时配置。

来源优先级（高 → 低）：CLI 参数 > 环境变量 > TOML 文件 > 内置默认值。
TOML 文件路径由调用方决定，约定默认 `/etc/dn42-control/agent.toml`。
"""

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib  # type: ignore[no-redef]

from .errors import ConfigError


AgentMode = Literal["apply", "write-rendered", "plan-only"]
DEFAULT_STATE_DIR = Path("/var/lib/dn42-control")

_AGENT_MODES: frozenset[str] = frozenset(("apply", "write-rendered", "plan-only"))


@dataclass(slots=True)
class AgentConfig:
    """Agent 单次运行所需的完整配置。

    字段语义见 docs/node-agent.md。``mode`` 决定一次 reconcile 走多远：

    - ``apply``（默认）：写盘 + 部署容器 + 本机收敛。
    - ``write-rendered``：只写渲染文件，不碰容器（无 Docker 的演示 / 联调环境）。
    - ``plan-only``：只校验 / 渲染 / 规划，不写盘、不部署（诊断）。
    """

    controller_url: str | None = None
    enrollment_token: str | None = None
    requested_node_id: str | None = None
    hostname: str | None = None
    state_dir: Path = field(default_factory=lambda: DEFAULT_STATE_DIR)
    rendered_dir: Path | None = None
    mode: AgentMode = "apply"
    log_level: str = "INFO"
    desired_state_path: Path | None = None
    http_timeout_seconds: float = 10.0
    local_convergence: bool = True
    # 路由全表周期采集间隔（秒）。独立于 reconcile 的纯观测；设 0 关闭采集。
    routing_interval_seconds: float = 300.0

    def with_overrides(self, **overrides: Any) -> "AgentConfig":
        """按非 None 的覆盖项返回新的配置副本。"""

        clean = {key: value for key, value in overrides.items() if value is not None}
        return replace(self, **clean)


def load_agent_config(toml_path: Path | None = None) -> AgentConfig:
    """从 TOML 文件 + 环境变量加载基础配置。

    - 不存在的文件不报错，相当于跳过该来源。
    - 仅识别白名单字段，多余键名视为配置错误以避免静默拼写问题。
    """

    config = AgentConfig()
    if toml_path is not None and toml_path.exists():
        config = _apply_toml(config, toml_path)
    config = _apply_env(config, os.environ)
    _validate_choices(config)
    return config


def _validate_choices(config: AgentConfig) -> None:
    """校验枚举型字段取值，避免非法配置静默流入运行期。"""

    if config.mode not in _AGENT_MODES:
        raise ConfigError(f"mode must be one of {sorted(_AGENT_MODES)}, got {config.mode!r}")


_ALLOWED_KEYS = {
    "controller_url",
    "enrollment_token",
    "requested_node_id",
    "hostname",
    "state_dir",
    "rendered_dir",
    "mode",
    "log_level",
    "desired_state_path",
    "http_timeout_seconds",
    "local_convergence",
    "routing_interval_seconds",
}


def _apply_toml(config: AgentConfig, toml_path: Path) -> AgentConfig:
    with toml_path.open("rb") as file:
        payload = tomllib.load(file)

    agent_section = payload.get("agent", payload)
    if not isinstance(agent_section, dict):
        raise ConfigError(f"agent config in {toml_path} must be a table")

    unknown = sorted(set(agent_section.keys()) - _ALLOWED_KEYS)
    if unknown:
        raise ConfigError(f"unknown agent config keys in {toml_path}: {', '.join(unknown)}")

    overrides: dict[str, Any] = {}
    for key, value in agent_section.items():
        if key in {"state_dir", "rendered_dir", "desired_state_path"} and value is not None:
            overrides[key] = Path(value)
        else:
            overrides[key] = value
    return config.with_overrides(**overrides)


_ENV_KEYS: dict[str, str] = {
    "DN42_AGENT_CONTROLLER_URL": "controller_url",
    "DN42_AGENT_ENROLLMENT_TOKEN": "enrollment_token",
    "DN42_AGENT_REQUESTED_NODE_ID": "requested_node_id",
    "DN42_AGENT_HOSTNAME": "hostname",
    "DN42_AGENT_STATE_DIR": "state_dir",
    "DN42_AGENT_RENDERED_DIR": "rendered_dir",
    "DN42_AGENT_MODE": "mode",
    "DN42_AGENT_LOG_LEVEL": "log_level",
    "DN42_AGENT_HTTP_TIMEOUT_SECONDS": "http_timeout_seconds",
    "DN42_AGENT_LOCAL_CONVERGENCE": "local_convergence",
    "DN42_AGENT_ROUTING_INTERVAL_SECONDS": "routing_interval_seconds",
}


def _apply_env(config: AgentConfig, env: dict[str, str] | os._Environ[str]) -> AgentConfig:
    overrides: dict[str, Any] = {}
    for env_key, field_name in _ENV_KEYS.items():
        if env_key not in env:
            continue
        raw = env[env_key]
        if field_name in {"state_dir", "rendered_dir"}:
            overrides[field_name] = Path(raw)
        elif field_name in {"http_timeout_seconds", "routing_interval_seconds"}:
            try:
                overrides[field_name] = float(raw)
            except ValueError as exc:
                raise ConfigError(f"{env_key} must be a number, got {raw!r}") from exc
        elif field_name == "local_convergence":
            overrides[field_name] = raw.strip().lower() in {"1", "true", "yes", "on"}
        else:
            overrides[field_name] = raw
    return config.with_overrides(**overrides)


__all__ = ["AgentConfig", "AgentMode", "DEFAULT_STATE_DIR", "load_agent_config"]
