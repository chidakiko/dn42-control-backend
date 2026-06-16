from __future__ import annotations

"""持久化最近一次成功使用的 Desired State，用于离线 plan / 故障回退。"""

import json
from pathlib import Path

from dn42_common import atomic_write_json
from dn42_schemas import DesiredState
from pydantic import ValidationError

from ..core.errors import DesiredStateError


def save_cached_desired_state(state: DesiredState, path: Path) -> None:
    """原子写入 Desired State 副本。"""

    atomic_write_json(path, state.model_dump(mode="json"))


def load_cached_desired_state(path: Path) -> DesiredState | None:
    """读取上次缓存的 Desired State；不存在或损坏时返回 `None`。"""

    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return DesiredState.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DesiredStateError(f"cached desired-state at {path} is corrupt: {exc}") from exc


__all__ = ["load_cached_desired_state", "save_cached_desired_state"]
