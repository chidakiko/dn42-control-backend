from __future__ import annotations

"""把渲染产物按 `FilePlan` 落盘到 rendered_dir。

执行端不做决策：写哪些、删哪些完全由传入的计划决定（决策在
`agent.planner`，文件计划带 prune——被删除资源的孤儿 `.conf` 真正从磁盘
消失，否则会在 wg-gateway 重建后的全量重放中把已删隧道重新拉起来）。
"""

import stat
from pathlib import Path

from dn42_runtime import ApplyOutcome, FilePlan, execute_file_plan

from ..core.logging import get_logger
from ..render.pipeline import RenderedBundle

_LOGGER = get_logger("writer")


def write_rendered_bundle(
    bundle: RenderedBundle,
    rendered_dir: Path,
    *,
    file_plan: FilePlan,
) -> ApplyOutcome:
    """严格按 `file_plan` 写入/删除文件，返回真实执行结果（供上报同源使用）。"""

    rendered_dir.mkdir(parents=True, exist_ok=True)
    outcome = execute_file_plan(file_plan, bundle.files, rendered_dir)
    for error in outcome.errors:
        _LOGGER.warning("write rendered bundle: %s", error)
    _mark_scripts_executable(rendered_dir)
    return outcome


def _mark_scripts_executable(rendered_dir: Path) -> None:
    scripts_dir = rendered_dir / "scripts"
    if not scripts_dir.exists():
        return

    for script in scripts_dir.rglob("*.sh"):
        if not script.is_file():
            continue
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


__all__ = ["write_rendered_bundle"]
