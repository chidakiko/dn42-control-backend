from __future__ import annotations

"""从本地文件加载 Desired State。"""

import json
from pathlib import Path

from dn42_schemas import DesiredState
from pydantic import ValidationError

from ..core.errors import DesiredStateError


def load_desired_state_from_file(path: Path) -> DesiredState:
    """读取并校验本地 JSON 文件中的 Desired State。"""

    if not path.exists():
        raise DesiredStateError(f"desired-state file does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
    except json.JSONDecodeError as exc:
        raise DesiredStateError(f"desired-state file {path} is not valid JSON: {exc}") from exc
    try:
        return DesiredState.model_validate(payload)
    except ValidationError as exc:
        raise DesiredStateError(f"desired-state file {path} failed validation: {exc}") from exc


__all__ = ["load_desired_state_from_file"]
