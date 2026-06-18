from __future__ import annotations

"""node-agent CLI（``agent.main``）模式与守护进程默认行为测试。"""

import pytest

from agent.main import _build_parser, _config_from_args, main


def _parse(args: list[str]):
    return _build_parser().parse_args(args)


def test_default_mode_is_apply() -> None:
    config = _config_from_args(_parse(["--controller-url", "http://c"]))
    assert config.mode == "apply"


def test_plan_only_sets_mode() -> None:
    config = _config_from_args(_parse(["--plan-only"]))
    assert config.mode == "plan-only"


def test_once_keeps_apply_mode() -> None:
    config = _config_from_args(_parse(["--once"]))
    assert config.mode == "apply"


def test_mode_flag_selects_write_rendered() -> None:
    config = _config_from_args(_parse(["--mode", "write-rendered", "--controller-url", "http://c"]))
    assert config.mode == "write-rendered"


def test_plan_only_conflicts_with_other_mode() -> None:
    with pytest.raises(SystemExit):
        _config_from_args(_parse(["--plan-only", "--mode", "apply"]))


def test_controller_and_desired_state_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        _config_from_args(
            _parse(["--controller-url", "http://c", "--desired-state", "/tmp/x.json"])
        )


def test_daemon_requires_controller_url(monkeypatch) -> None:
    # 清掉可能存在的环境变量，确保默认无 controller_url
    for key in list(__import__("os").environ):
        if key.startswith("DN42_AGENT_"):
            monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit):
        main([])


def test_daemon_rejects_plan_only_mode(monkeypatch) -> None:
    for key in list(__import__("os").environ):
        if key.startswith("DN42_AGENT_"):
            monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit):
        main(["--controller-url", "http://c", "--mode", "plan-only"])


def test_legacy_flags_are_gone() -> None:
    with pytest.raises(SystemExit):
        _parse(["--watch"])
    with pytest.raises(SystemExit):
        _parse(["--apply-local"])
