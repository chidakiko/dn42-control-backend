from __future__ import annotations

"""Agent 内部使用的结构化异常类型。

异常被分层组织，便于上层根据语义决定 retry / abort / report。"""


class AgentError(Exception):
    """所有 agent 自定义异常的基类。"""


class ConfigError(AgentError):
    """加载或合并 agent 配置时出现的错误。"""


class ControllerError(AgentError):
    """与 Control Server 通讯时出现的错误。

    Attributes:
        status_code: HTTP 状态码（非 HTTP 层错误时为 ``None``）。
            Session 据此区分"凭据失效（401，可自愈）"与其他故障。
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class BootstrapPendingError(ControllerError):
    """注册请求被 Control Server 标记为待审批。"""


class BootstrapRejectedError(ControllerError):
    """注册请求被 Control Server 拒绝。"""


class DesiredStateError(AgentError):
    """加载、缓存或校验 Desired State 失败。"""


class RenderError(AgentError):
    """模板渲染失败。"""


class ApplyError(AgentError):
    """执行 apply plan 时失败。"""


__all__ = [
    "AgentError",
    "ApplyError",
    "BootstrapPendingError",
    "BootstrapRejectedError",
    "ConfigError",
    "ControllerError",
    "DesiredStateError",
    "RenderError",
]
