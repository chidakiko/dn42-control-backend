from __future__ import annotations

"""本机收敛的**执行**：把 `ConvergencePlan` 照单翻译成容器内 exec。

决策在 `agent.planner.convergence_plan` 里完成；本模块不做任何判断，
只把动作清单逐条执行并收集结果。

原则：

- **best-effort**：任一步失败只记录、收进结果，绝不让整个 reconcile 崩。
- **可注入**：所有容器内执行走注入式 ``ContainerExec``（生产为 Docker SDK），
  单测无需真实 docker。
"""

from dataclasses import dataclass, field

from ..core.exec import ContainerExec, ExecResult
from ..core.logging import get_logger
from ..planner.convergence_plan import ConvergenceAction, ConvergenceKind, ConvergencePlan

_LOGGER = get_logger("convergence")


@dataclass(frozen=True, slots=True)
class ConvergenceStep:
    target: str
    command: list[str]
    returncode: int
    ok: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ConvergenceResult:
    ok: bool
    steps: list[ConvergenceStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "steps": [
                {
                    "target": step.target,
                    "command": step.command,
                    "returncode": step.returncode,
                    "ok": step.ok,
                    "error": step.error,
                }
                for step in self.steps
            ],
        }


def execute_convergence_plan(
    plan: ConvergencePlan,
    *,
    container_exec: ContainerExec,
) -> ConvergenceResult:
    """严格按计划执行收敛动作；空计划即不执行任何命令。"""

    steps = [_exec_step(container_exec, action) for action in plan.actions]
    return ConvergenceResult(ok=all(step.ok for step in steps), steps=steps)


def _action_command(action: ConvergenceAction) -> tuple[str, str, list[str]]:
    """把单个收敛动作翻译成 (日志 target, 容器名, 容器内 argv)。"""

    if action.kind == ConvergenceKind.BIRD_RELOAD:
        return (
            f"bird:{action.container}",
            action.container,
            ["birdc", "configure"],
        )
    if action.kind == ConvergenceKind.WG_SYNC_LOOPBACK:
        # loopback（dn42-lo）由固定脚本名拉起；"全量拉起"由计划层展开成
        # loopback + 逐接口的独立动作，这里只负责执行单个脚本。
        return (
            f"wg:{action.container}:loopback",
            action.container,
            ["sh", "/opt/dn42/scripts/wg/apply-dn42-lo.sh"],
        )
    if action.kind == ConvergenceKind.WG_SYNC_INTERFACE:
        assert action.interface is not None
        return (
            f"wg:{action.container}:{action.interface}",
            action.container,
            ["sh", f"/opt/dn42/scripts/wg/apply-{action.interface}.sh"],
        )
    if action.kind == ConvergenceKind.WG_REMOVE_INTERFACE:
        assert action.interface is not None
        return (
            f"wg:{action.container}:{action.interface}:remove",
            action.container,
            ["ip", "link", "del", action.interface],
        )
    raise ValueError(f"unknown convergence action kind: {action.kind}")


def _exec_step(container_exec: ContainerExec, action: ConvergenceAction) -> ConvergenceStep:
    target, container, argv = _action_command(action)
    try:
        result: ExecResult = container_exec.run(container, argv)
    except Exception as exc:  # noqa: BLE001 - 收敛是 best-effort
        _LOGGER.warning("convergence step failed to launch on %s: %s", target, exc)
        return ConvergenceStep(
            target=target, command=argv, returncode=-1, ok=False, error=str(exc)
        )
    returncode, _stdout, stderr = result
    ok = returncode == 0
    if not ok:
        _LOGGER.warning(
            "convergence step on %s exited %s: %s", target, returncode, stderr.strip()
        )
    return ConvergenceStep(
        target=target,
        command=argv,
        returncode=returncode,
        ok=ok,
        error=None if ok else (stderr.strip() or f"exit {returncode}"),
    )


__all__ = [
    "ConvergenceResult",
    "ConvergenceStep",
    "execute_convergence_plan",
]
