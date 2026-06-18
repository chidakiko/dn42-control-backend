from __future__ import annotations

""":class:`AgentConfig` 加载与覆写顺序的单元测试。

节点 agent 的配置优先级是：``默认 < TOML < CLI 覆写 < 环境变量``。
本文件锁定以下不变量：

* ``load_agent_config(path)`` 在文件不存在时返回全默认，不报错；
* TOML 文件中的 ``[agent]`` 区块被胶合完整读取、包括
  ``state_dir`` 路径、``mode`` 选择；
* 出现未知 key 时会报 ``ConfigError``，避免手误参数静默被丢弃；
* ``with_overrides(...)``：传 None 时维持原值，非 None 时覆写原值；
* 环境变量 ``DN42_AGENT_*`` 在 TOML 之后作用，体现优先级。
"""

from pathlib import Path

import pytest

from agent.core.config import AgentConfig, load_agent_config
from agent.core.errors import ConfigError


def test_load_agent_config_returns_defaults_when_file_missing(tmp_path: Path) -> None:
    config = load_agent_config(tmp_path / "missing.toml")

    assert config.controller_url is None
    assert config.mode == "apply"
    assert config.state_dir == AgentConfig().state_dir


def test_load_agent_config_reads_toml_file(tmp_path: Path) -> None:
    path = tmp_path / "agent.toml"
    path.write_text(
        """
        [agent]
        controller_url = "https://control.example"
        enrollment_token = "token"
        state_dir = "/srv/dn42"
        mode = "write-rendered"
        """,
        encoding="utf-8",
    )

    config = load_agent_config(path)

    assert config.controller_url == "https://control.example"
    assert config.enrollment_token == "token"
    assert config.state_dir == Path("/srv/dn42")
    assert config.mode == "write-rendered"


def test_load_agent_config_rejects_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "agent.toml"
    path.write_text("[agent]\nbogus = 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown agent config keys"):
        load_agent_config(path)


def test_with_overrides_keeps_existing_values_when_override_is_none() -> None:
    config = AgentConfig(controller_url="https://existing")

    updated = config.with_overrides(controller_url=None, hostname="hkg1")

    assert updated.controller_url == "https://existing"
    assert updated.hostname == "hkg1"


def test_env_overrides_apply_after_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "agent.toml"
    path.write_text("[agent]\ncontroller_url = \"https://from-toml\"\n", encoding="utf-8")
    monkeypatch.setenv("DN42_AGENT_CONTROLLER_URL", "https://from-env")
    monkeypatch.setenv("DN42_AGENT_MODE", "write-rendered")
    monkeypatch.setenv("DN42_AGENT_HTTP_TIMEOUT_SECONDS", "120")

    config = load_agent_config(path)

    assert config.controller_url == "https://from-env"
    assert config.mode == "write-rendered"
    assert config.http_timeout_seconds == 120.0


def test_env_rejects_invalid_http_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DN42_AGENT_HTTP_TIMEOUT_SECONDS", "slow")

    with pytest.raises(ConfigError, match="DN42_AGENT_HTTP_TIMEOUT_SECONDS must be a number"):
        load_agent_config(None)


def test_mode_defaults_to_apply_and_reads_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert load_agent_config(None).mode == "apply"

    monkeypatch.setenv("DN42_AGENT_MODE", "write-rendered")
    assert load_agent_config(None).mode == "write-rendered"


def test_invalid_mode_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DN42_AGENT_MODE", "yolo")

    with pytest.raises(ConfigError, match="mode must be one of"):
        load_agent_config(None)


def test_removed_deploy_backend_key_is_rejected(tmp_path: Path) -> None:
    """deploy_backend 已随 compose-cli 后端移除,残留配置应显式报错而非静默忽略。"""

    path = tmp_path / "agent.toml"
    path.write_text('[agent]\ndeploy_backend = "docker-api"\n', encoding="utf-8")

    with pytest.raises(ConfigError, match="unknown agent config keys"):
        load_agent_config(path)
