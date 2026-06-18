from __future__ import annotations

"""Agent 注册 / 鉴权 token 的形状校验。

控制面下发给 Agent 的 enrollment / agent token 约定为 base64url（RFC 4648
`-_` 字母表，无 padding），并要求足够的最小熵长度，避免把弱口令或被截断的
token 放进 schema。本模块只做格式与长度校验，不验证签名或服务端是否签发。
"""

import re

# base64url 无 padding：A-Z a-z 0-9 - _
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
# 22 个 base64url 字符约编码 132 bit，远超常见 128-bit 熵门槛。
_MIN_TOKEN_LENGTH = 22
_MAX_TOKEN_LENGTH = 512


def validate_agent_token(value: str) -> str:
    """校验 Agent token 是否为合法的 base64url 字面量且长度达标。

    返回原值。失败抛 `ValueError`，错误信息中不包含 token 本身，避免日志泄露。
    """

    if not isinstance(value, str):
        raise TypeError(f"agent token must be str, got {type(value).__name__}")
    if len(value) < _MIN_TOKEN_LENGTH:
        raise ValueError(
            f"agent token must be at least {_MIN_TOKEN_LENGTH} characters of base64url"
        )
    if len(value) > _MAX_TOKEN_LENGTH:
        raise ValueError(
            f"agent token must be at most {_MAX_TOKEN_LENGTH} characters"
        )
    if not _TOKEN_PATTERN.fullmatch(value):
        raise ValueError("agent token contains characters outside the base64url alphabet")
    return value


def is_agent_token(value: object) -> bool:
    """与 `validate_agent_token` 等价的非抛出版本。"""

    if not isinstance(value, str):
        return False
    try:
        validate_agent_token(value)
    except (ValueError, TypeError):
        return False
    return True


__all__ = ["is_agent_token", "validate_agent_token"]
