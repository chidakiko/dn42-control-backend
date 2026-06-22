from __future__ import annotations

"""把渲染产物转化为相对 rendered 目录的写盘计划。"""

from pathlib import Path

from dn42_runtime import FilePlan, build_file_plan

from ..render.pipeline import RenderedBundle


def build_file_plan_for_state(bundle: RenderedBundle, rendered_dir: Path | None) -> FilePlan:
    """生成一份 create/update/noop 计划。"""

    return build_file_plan(bundle.files, rendered_dir)


__all__ = ["build_file_plan_for_state"]
