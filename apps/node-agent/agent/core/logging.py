from __future__ import annotations

"""Agent 统一日志配置。"""

import logging
import sys


_LOGGER_NAMESPACE = "dn42.agent"
_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def configure_logging(level: str | int = "INFO") -> None:
    """配置 agent 命名空间下的根 logger，避免污染全局 root。

    重复调用是幂等的：会清除已存在的 handlers 后重新装配。
    """

    logger = logging.getLogger(_LOGGER_NAMESPACE)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(level if isinstance(level, int) else level.upper())
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """获取 agent 命名空间下的子 logger。"""

    return logging.getLogger(f"{_LOGGER_NAMESPACE}.{name}")


__all__ = ["configure_logging", "get_logger"]
