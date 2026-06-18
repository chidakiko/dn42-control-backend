from __future__ import annotations

"""DN42 Node Agent 公共门面。

第一层公开符号都在这里再导出，外部代码（CLI / 测试 / 控制面集成）
应该尽量只通过 `agent.X` 形式导入，避免依赖内部模块路径。
"""

from .adapters import Adapters
from .apply import (
    ApplyExecutor,
    DeployResult,
    DockerApiBackend,
)
from .client import ControllerClient
from .collectors import (
    DockerObserver,
    ObservedProject,
    build_host_inventory,
    build_runtime_snapshot,
)
from .core import (
    AgentConfig,
    AgentError,
    AgentPaths,
    ApplyError,
    BootstrapPendingError,
    BootstrapRejectedError,
    ConfigError,
    ControllerError,
    DesiredStateError,
    LocalAgentIdentity,
    RenderError,
    node_project_name,
    configure_logging,
    get_logger,
    load_agent_config,
    load_identity,
    save_identity,
    service_container_name,
    utc_now,
    utc_now_iso,
)
from .desired_state import (
    load_cached_desired_state,
    load_desired_state_from_file,
    save_cached_desired_state,
)
from .health import build_reconciliation_report
from .orchestrator import (
    OrchestratorResult,
    ReconcileOrchestrator,
    run_once,
)
from .planner import (
    ContainerAction,
    ContainerPlan,
    ConvergencePlan,
    ReconcilePlan,
    build_container_plan,
    build_convergence_plan,
    build_file_plan_for_state,
    build_reconcile_plan,
)
from .render import RenderedBundle, render_state
from .session import Session
from .sources import DesiredStateSource, select_source


__version__ = "0.2.0"


__all__ = [
    "Adapters",
    "AgentConfig",
    "AgentError",
    "AgentPaths",
    "ApplyError",
    "ApplyExecutor",
    "BootstrapPendingError",
    "BootstrapRejectedError",
    "ConfigError",
    "ContainerAction",
    "ContainerPlan",
    "ControllerClient",
    "ControllerError",
    "ConvergencePlan",
    "DeployResult",
    "DesiredStateError",
    "DesiredStateSource",
    "DockerApiBackend",
    "DockerObserver",
    "LocalAgentIdentity",
    "ObservedProject",
    "OrchestratorResult",
    "ReconcileOrchestrator",
    "ReconcilePlan",
    "RenderError",
    "RenderedBundle",
    "Session",
    "__version__",
    "build_container_plan",
    "build_convergence_plan",
    "build_file_plan_for_state",
    "build_host_inventory",
    "build_reconcile_plan",
    "build_reconciliation_report",
    "build_runtime_snapshot",
    "node_project_name",
    "configure_logging",
    "get_logger",
    "load_agent_config",
    "load_cached_desired_state",
    "load_desired_state_from_file",
    "load_identity",
    "render_state",
    "run_once",
    "save_cached_desired_state",
    "save_identity",
    "select_source",
    "service_container_name",
    "utc_now",
    "utc_now_iso",
]
