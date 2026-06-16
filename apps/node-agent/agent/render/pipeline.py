from __future__ import annotations

"""薄封装 `dn42_templates.render_desired_state`。"""

from dataclasses import dataclass

from dn42_runtime import RenderedFile
from dn42_schemas import DesiredState
from dn42_templates import render_desired_state

from ..core.errors import RenderError


@dataclass(frozen=True, slots=True)
class RenderedBundle:
    """单次渲染的全部产物。"""

    files: list[RenderedFile]


def render_state(state: DesiredState) -> RenderedBundle:
    """渲染 Desired State；失败时统一抛 `RenderError`。"""

    try:
        files = render_desired_state(state)
    except Exception as exc:
        raise RenderError(f"failed to render desired state: {exc}") from exc
    return RenderedBundle(files=files)


__all__ = ["RenderedBundle", "render_state"]
