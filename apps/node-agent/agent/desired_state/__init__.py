from __future__ import annotations

"""Desired State 加载、缓存与校验。"""

from .cache import load_cached_desired_state, save_cached_desired_state
from .loader import load_desired_state_from_file


__all__ = [
    "load_cached_desired_state",
    "load_desired_state_from_file",
    "save_cached_desired_state",
]
