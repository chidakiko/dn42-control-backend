from __future__ import annotations

"""``agent --doctor`` 自检的单元测试。

控制面 / Docker 探针通过依赖注入替换为假实现，自检完全脱离网络与 Docker。
"""

from pathlib import Path

from agent.core.config import AgentConfig
from agent.core.identity import LocalAgentIdentity, save_identity
from agent.core.paths import AgentPaths
from agent.doctor import Check, run_doctor
from agent.metrics import record_reconcile


def _config(tmp_path: Path, **overrides) -> AgentConfig:
    base = dict(
        controller_url="http://controller.example:8000",
        requested_node_id="edge1",
        state_dir=tmp_path,
    )
    base.update(overrides)
    return AgentConfig(**base)


def _ok_controller(_config: AgentConfig) -> Check:
    return Check(name="controller", ok=True, detail={"status_code": 200})


def _bad_controller(_config: AgentConfig) -> Check:
    return Check(name="controller", ok=False, detail={"error": "refused"})


def _ok_docker(_config: AgentConfig) -> Check:
    return Check(name="docker", ok=True, detail={"reachable": True})


def _bad_docker(_config: AgentConfig) -> Check:
    return Check(name="docker", ok=False, detail={"reachable": False})


def _checks_by_name(report) -> dict[str, Check]:
    return {c.name: c for c in report.checks}


def test_healthy_node_reports_ok(tmp_path: Path) -> None:
    config = _config(tmp_path)
    paths = AgentPaths(config.state_dir, "edge1")
    paths.node_dir.mkdir(parents=True, exist_ok=True)
    save_identity(
        LocalAgentIdentity(node_id="edge1", agent_id="a", agent_token="tok"),
        paths.identity_file,
    )

    report = run_doctor(config, controller_probe=_ok_controller, docker_probe=_ok_docker)
    assert report.ok is True
    checks = _checks_by_name(report)
    assert checks["config"].ok
    assert checks["state_dir"].ok
    assert checks["identity"].detail["has_token"] is True
    assert checks["controller"].ok
    assert checks["docker"].ok


def test_unreachable_controller_fails(tmp_path: Path) -> None:
    config = _config(tmp_path)
    report = run_doctor(config, controller_probe=_bad_controller, docker_probe=_ok_docker)
    assert report.ok is False


def test_docker_only_probed_in_apply_mode(tmp_path: Path) -> None:
    config = _config(tmp_path, mode="write-rendered")
    report = run_doctor(config, controller_probe=_ok_controller, docker_probe=_bad_docker)
    # write-rendered 不碰容器，不跑 docker 探针 → 不因 docker 失败。
    assert "docker" not in _checks_by_name(report)
    assert report.ok is True


def test_missing_source_config_fails(tmp_path: Path) -> None:
    config = AgentConfig(state_dir=tmp_path, requested_node_id="n1")  # 无 controller / desired_state
    report = run_doctor(config, controller_probe=_ok_controller, docker_probe=_ok_docker)
    assert _checks_by_name(report)["config"].ok is False
    assert report.ok is False


def test_identity_missing_is_informational_not_fatal(tmp_path: Path) -> None:
    # 全新节点没有身份文件：identity 非 critical，不应让 doctor 整体失败。
    config = _config(tmp_path)
    report = run_doctor(config, controller_probe=_ok_controller, docker_probe=_ok_docker)
    identity = _checks_by_name(report)["identity"]
    assert identity.critical is False
    assert identity.detail["has_token"] is False
    assert report.ok is True


def test_metrics_surfaced_in_report(tmp_path: Path) -> None:
    config = _config(tmp_path)
    record_reconcile(
        AgentPaths(config.state_dir, "edge1").metrics_file,
        status="succeeded",
        duration_seconds=1.5,
        generation=4,
    )
    report = run_doctor(config, controller_probe=_ok_controller, docker_probe=_ok_docker)
    metrics = _checks_by_name(report)["metrics"]
    assert metrics.detail["available"] is True
    assert metrics.detail["last_generation"] == 4
