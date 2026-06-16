from __future__ import annotations

"""路由全表的周期采集与上报——**独立于 reconcile** 的纯观测路径。

刻意与 :mod:`agent.orchestrator` 解耦：路由全表数据量大、变化频繁，且与"达成
期望态"无关。它绝不走 doorbell / consumer 那条唯一的 reconcile 入口，也不参与
``applied_generation``，因此不会触发任何 apply / 抖动。常驻循环以自己的节奏
（``routing_interval_seconds``）调用这里，采集失败只记日志、不影响收敛。
"""

import logging

from .adapters import Adapters
from .collectors.routing import collect_routing_snapshot
from .core.clock import utc_now_iso
from .core.config import AgentConfig
from .core.paths import AgentPaths
from .desired_state.cache import load_cached_desired_state

logger = logging.getLogger(__name__)


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

    snapshot = collect_routing_snapshot(
        state, adapters.container_exec, captured_at=utc_now_iso()
    )
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
