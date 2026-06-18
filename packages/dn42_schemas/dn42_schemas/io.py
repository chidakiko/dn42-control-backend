from __future__ import annotations

"""从本地文件加载并校验 `DesiredState` 的 IO 辅助。

支持 `.json` / `.yaml` / `.yml` 三种扩展名：JSON 走标准库，YAML 在调用时按需
导入 `PyYAML`（缺失时只针对 YAML 文件给出明确错误，不影响 JSON 路径）。所有
加载结果都会经过 `DesiredState.model_validate`，把结构与跨字段引用校验前置。
"""

import json
from pathlib import Path
from typing import Any

from .desired_state import DesiredState


def load_desired_state(path: str | Path) -> DesiredState:
    """读取并校验本地文件中的 `DesiredState`（按扩展名识别 JSON/YAML）。"""

    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"desired-state file does not exist: {file_path}")

    payload = _load_payload(file_path)
    return DesiredState.model_validate(payload)


def _load_payload(file_path: Path) -> Any:
    suffix = file_path.suffix.lower()
    text = file_path.read_text(encoding="utf-8")
    if suffix in (".yaml", ".yml"):
        return _load_yaml(text, file_path)
    if suffix == ".json":
        return _load_json(text, file_path)
    # 未知扩展名：先尝试 JSON，失败再尝试 YAML，给调用方最大兼容性。
    try:
        return _load_json(text, file_path)
    except ValueError:
        return _load_yaml(text, file_path)


def _load_json(text: str, file_path: Path) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"desired-state file {file_path} is not valid JSON: {exc}") from exc


def _load_yaml(text: str, file_path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            f"loading YAML desired-state {file_path} requires PyYAML to be installed"
        ) from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"desired-state file {file_path} is not valid YAML: {exc}") from exc


__all__ = ["load_desired_state"]
