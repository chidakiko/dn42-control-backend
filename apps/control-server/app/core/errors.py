from __future__ import annotations

"""控制服务器内置异常类型。"""


class ControlServerError(Exception):
    """所有 control-server 自定义异常的基类。"""


class UnknownNodeError(ControlServerError):
    """目标 `node_id` 在控制面没有任何记录。"""


class InvalidEnrollmentTokenError(ControlServerError):
    """注册请求携带了非法 enrollment token。"""


__all__ = [
    "ControlServerError",
    "InvalidEnrollmentTokenError",
    "UnknownNodeError",
]
