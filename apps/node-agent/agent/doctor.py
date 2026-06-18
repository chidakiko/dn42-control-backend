from __future__ import annotations

"""``agent --doctor``：一次性自检，把"这个节点能不能正常收敛"摊开给运维。

不改任何状态，只读：配置是否自洽、状态目录是否可写、本地身份/注册是否就绪、
控制面是否可达、Docker 是否可用、最近 reconcile 指标如何。每项产出一个
``Check``；``report.ok`` 仅由 *critical* 项决定（配置 / 状态目录 / 控制面 /
Docker），信息项（身份 / 指标）只展示不参与判定。

控制面与 Docker 探测通过依赖注入，单测传入假探针即可完全脱离网络与 Docker。
"""

from dataclasses import dataclass, field
from typing import Any, Callable

from .core.config import AgentConfig
from .core.identity import load_identity
from .core.paths import AgentPaths
from .metrics import load_metrics
from .watch import resolve_node_id


@dataclass(slots=True)
class Check:
    """单项自检结果。``critical`` 项才参与 ``report.ok`` 判定。"""

    name: str
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    critical: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "critical": self.critical, "detail": self.detail}


@dataclass(slots=True)
class DoctorReport:
    ok: bool
    checks: list[Check]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "checks": [check.to_dict() for check in self.checks]}


ControllerProbe = Callable[[AgentConfig], Check]
DockerProbe = Callable[[AgentConfig], Check]


def _check_config(config: AgentConfig) -> Check:
    has_source = config.controller_url is not None or config.desired_state_path is not None
    return Check(
        name="config",
        ok=has_source,
        detail={
            "mode": config.mode,
            "controller_url": config.controller_url,
            "desired_state_path": str(config.desired_state_path)
            if config.desired_state_path is not None
            else None,
            "has_enrollment_token": config.enrollment_token is not None,
        },
    )


def _check_state_dir(config: AgentConfig) -> Check:
    nodes_dir = config.state_dir / "nodes"
    try:
        nodes_dir.mkdir(parents=True, exist_ok=True)
        writable = True
        error: str | None = None
    except OSError as exc:
        writable = False
        error = str(exc)
    detail: dict[str, Any] = {"state_dir": str(config.state_dir), "writable": writable}
    if error is not None:
        detail["error"] = error
    return Check(name="state_dir", ok=writable, detail=detail)


def _check_identity(config: AgentConfig) -> Check:
    """身份/注册状态——信息项，未注册的全新节点不算失败。"""

    node_id = resolve_node_id(config)
    if node_id is None:
        return Check(
            name="identity",
            ok=False,
            detail={"node_id": None, "reason": "node_id 未确定（未注册或多节点目录歧义）"},
            critical=False,
        )
    identity = load_identity(AgentPaths(config.state_dir, node_id).identity_file)
    has_token = identity.agent_token is not None
    return Check(
        name="identity",
        ok=has_token,
        detail={
            "node_id": identity.node_id or node_id,
            "agent_id": identity.agent_id,
            "has_token": has_token,
            "applied_generation": identity.applied_generation,
            "last_apply_status": identity.last_apply_status,
            "last_apply_at": identity.last_apply_at,
        },
        critical=False,
    )


def _check_metrics(config: AgentConfig) -> Check:
    """最近 reconcile 指标——纯信息项。"""

    node_id = resolve_node_id(config)
    if node_id is None:
        return Check(name="metrics", ok=True, detail={"available": False}, critical=False)
    metrics = load_metrics(AgentPaths(config.state_dir, node_id).metrics_file)
    return Check(
        name="metrics",
        ok=True,
        detail={
            "available": metrics.total_reconciles > 0,
            "total_reconciles": metrics.total_reconciles,
            "total_failures": metrics.total_failures,
            "consecutive_failures": metrics.consecutive_failures,
            "last_status": metrics.last_status,
            "last_duration_seconds": metrics.last_duration_seconds,
            "last_generation": metrics.last_generation,
            "last_reconcile_at": metrics.last_reconcile_at,
        },
        critical=False,
    )


def _default_controller_probe(config: AgentConfig) -> Check:
    assert config.controller_url is not None
    import httpx

    url = config.controller_url.rstrip("/") + "/api/v1/healthz"
    try:
        response = httpx.get(url, timeout=config.http_timeout_seconds)
        ok = response.status_code == 200
        return Check(name="controller", ok=ok, detail={"url": url, "status_code": response.status_code})
    except Exception as exc:  # noqa: BLE001 - 任何网络异常都视为不可达
        return Check(name="controller", ok=False, detail={"url": url, "error": str(exc)})


def _default_docker_probe(_config: AgentConfig) -> Check:
    try:
        import docker  # type: ignore[import-untyped]

        client = docker.from_env()
        client.ping()
        return Check(name="docker", ok=True, detail={"reachable": True})
    except Exception as exc:  # noqa: BLE001 - 缺 SDK / daemon 不可达统一报失败
        return Check(name="docker", ok=False, detail={"reachable": False, "error": str(exc)})


def run_doctor(
    config: AgentConfig,
    *,
    controller_probe: ControllerProbe = _default_controller_probe,
    docker_probe: DockerProbe = _default_docker_probe,
) -> DoctorReport:
    """跑全部自检并汇总。``report.ok`` 由 critical 项的 ``ok`` 求与得到。"""

    checks: list[Check] = [
        _check_config(config),
        _check_state_dir(config),
        _check_identity(config),
    ]
    if config.controller_url is not None:
        checks.append(controller_probe(config))
    if config.mode == "apply":
        checks.append(docker_probe(config))
    checks.append(_check_metrics(config))

    ok = all(check.ok for check in checks if check.critical)
    return DoctorReport(ok=ok, checks=checks)


__all__ = ["Check", "DoctorReport", "run_doctor"]
