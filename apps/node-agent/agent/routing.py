from __future__ import annotations

"""路由全表的周期采集与上报——**独立于 reconcile** 的纯观测路径。

刻意与 :mod:`agent.orchestrator` 解耦：路由全表数据量大、变化频繁，且与"达成
期望态"无关。它绝不走 doorbell / consumer 那条唯一的 reconcile 入口，也不参与
``applied_generation``，因此不会触发任何 apply / 抖动。常驻循环以自己的节奏
（``routing_interval_seconds``）调用这里，采集失败只记日志、不影响收敛。
"""

import logging
from pathlib import Path

from dn42_schemas import DesiredState, ServiceRole

from .adapters import Adapters
from .collectors.bird_socket import BirdSocketExec
from .collectors.routing import collect_routing_snapshot
from .core.clock import utc_now_iso
from .core.config import AgentConfig
from .core.paths import AgentPaths
from .desired_state.cache import load_cached_desired_state
from .planner.definition import resolve_volume_source

logger = logging.getLogger(__name__)

# bird 容器把控制 socket 运行目录（默认 /run/bird，内含 bird.ctl）以可写挂载暴露给宿主，
# agent 据此直连。target 与 bird daemon 的默认 socket 目录一致（apply-bird.sh: mkdir /run/bird）。
_BIRD_RUN_TARGET = "/run/bird"
_BIRD_SOCKET_FILE = "bird.ctl"


def _derive_bird_socket_path(state: DesiredState, rendered_dir: Path) -> str | None:
    """从 desired-state 里 bird 服务的 ``/run/bird`` 挂载推导宿主侧 socket 路径。

    与容器 bind 用同一套 ``resolve_volume_source`` 解析 source，保证 agent 连接的路径
    与 bird 写 socket 的宿主落点严格一致。bird 服务未声明该挂载（旧快照未升级）⇒ None。
    """

    for service in state.runtime.services:
        if service.role != ServiceRole.BIRD_ROUTER:
            continue
        for mount in service.volumes:
            if mount.target == _BIRD_RUN_TARGET:
                host_dir = Path(resolve_volume_source(rendered_dir, mount))
                return str(host_dir / _BIRD_SOCKET_FILE)
    return None


def collect_and_publish_routing(config: AgentConfig, adapters: Adapters, node_id: str) -> bool:
    """采集一次 BIRD 路由全表并上报控制面。

    依赖 reconcile 落盘的缓存 desired-state 拿到 BIRD 容器名（避免再打控制面）。
    无缓存（尚未成功 reconcile 过）时跳过本轮，返回 ``False``。
    """

    paths = AgentPaths(config.state_dir, node_id)
    state = load_cached_desired_state(paths.desired_state_file)
    if state is None:
        logger.debug("routing: 无缓存 desired-state，跳过本轮路由采集")
        return False

    # 路由采集直连 BIRD 控制 socket（不再经 docker exec 跑 birdc）。socket 路径默认从
    # bird 服务的 /run/bird 挂载推导；``bird_socket_path`` 配置项可显式覆盖。
    rendered_dir = config.rendered_dir or paths.rendered_dir
    socket_path = config.bird_socket_path or _derive_bird_socket_path(state, rendered_dir)
    if socket_path is None:
        logger.warning(
            "routing: bird 服务未声明 %s 控制 socket 挂载（快照待升级），跳过本轮路由采集",
            _BIRD_RUN_TARGET,
        )
        return False
    bird_exec = BirdSocketExec(socket_path, timeout=config.http_timeout_seconds)
    snapshot = collect_routing_snapshot(state, bird_exec, captured_at=utc_now_iso())
    if adapters.session is not None:
        adapters.session.call(lambda client: client.post_routing_table(snapshot))
    logger.info(
        "routing: 已采集路由全表 node=%s observation=%s routes=%d",
        node_id,
        snapshot.observation.value,
        len(snapshot.routes),
    )
    return True


__all__ = ["collect_and_publish_routing"]
