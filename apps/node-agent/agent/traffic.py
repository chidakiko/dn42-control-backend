from __future__ import annotations

"""WG 流量的 30s 轻量周期采集与上报——**独立于 reconcile** 的高频观测路径。

完整 ``RuntimeSnapshot`` 节奏数分钟一次、采全（容器 / 接口 / BGP），不适合画细粒度
吞吐曲线。本模块只跑一次 ``wg show all transfer``（最轻的 wg 查询，仅累计收 / 发字节），
把全部 peer 求和后上报 ``WireGuardTrafficSample``，让控制面以 ~30s 粒度画实时吞吐——
不必为高频流量去拉整份重快照。

与 :mod:`agent.routing` / :mod:`agent.reresolve` 同构：依赖 reconcile 落盘的缓存
desired-state 拿 wg-gateway 容器名（不打控制面），采集失败只记日志、循环继续，绝不
触碰 ``applied_generation`` / apply。上报 best-effort——旧控制面无此端点（404）时吞掉。
"""

import logging

from dn42_schemas import ServiceRole, WireGuardTrafficSample

from .adapters import Adapters
from .core.clock import utc_now_iso
from .core.config import AgentConfig
from .core.naming import service_container_by_role
from .core.paths import AgentPaths
from .desired_state.cache import load_cached_desired_state

logger = logging.getLogger(__name__)


def parse_wg_transfer(output: str) -> tuple[int, int, int]:
    """解析 ``wg show all transfer`` 输出为 ``(rx_total, tx_total, peer_count)``。

    每行 4 列：``<interface>\\t<public_key>\\t<rx_bytes>\\t<tx_bytes>``。字段无内部空白，
    按任意空白切分即安全；非数字 / 残缺行跳过（不臆造）。全部 peer 的收 / 发字节求和。
    """

    rx_total = tx_total = peer_count = 0
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        rx, tx = parts[2], parts[3]
        if not (rx.lstrip("-").isdigit() and tx.lstrip("-").isdigit()):
            continue
        rx_total += max(0, int(rx))
        tx_total += max(0, int(tx))
        peer_count += 1
    return rx_total, tx_total, peer_count


def collect_and_publish_traffic(
    config: AgentConfig, adapters: Adapters, node_id: str
) -> WireGuardTrafficSample | None:
    """采集一次 WG 流量累计计数并上报控制面；返回本次采样（无法采集时 ``None``）。

    依赖缓存 desired-state 拿 wg-gateway 容器名。无缓存 / 无 wg 容器 / 采集失败时返回
    ``None``，本轮跳过。上报失败（含旧控制面 404）只记日志，不影响后续轮次。
    """

    paths = AgentPaths(config.state_dir, node_id)
    state = load_cached_desired_state(paths.desired_state_file)
    if state is None:
        logger.debug("traffic: 无缓存 desired-state，跳过本轮流量采集")
        return None
    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    if wg_container is None:
        logger.debug("traffic: desired-state 无 wg-gateway 容器，跳过本轮")
        return None

    try:
        returncode, stdout, stderr = adapters.container_exec.run(
            wg_container, ["wg", "show", "all", "transfer"]
        )
    except Exception as exc:  # noqa: BLE001 - 容器不可达等统一降级
        logger.warning("traffic: wg show all transfer 异常（容器不可达？）：%s", exc)
        return None
    if returncode != 0:
        logger.warning("traffic: wg show all transfer rc=%s：%s", returncode, stderr.strip())
        return None

    rx, tx, peers = parse_wg_transfer(stdout)
    sample = WireGuardTrafficSample(
        node_id=node_id,
        captured_at=utc_now_iso(),
        rx_bytes=rx,
        tx_bytes=tx,
        peer_count=peers,
    )
    if adapters.session is not None:
        try:
            adapters.session.call(lambda client: client.post_wireguard_traffic(sample))
        except Exception:  # noqa: BLE001 - 上报 best-effort（含旧控制面 404）
            logger.warning("traffic: 上报控制面失败（忽略，不影响采集）", exc_info=True)
    logger.debug("traffic: node=%s rx=%d tx=%d peers=%d", node_id, rx, tx, peers)
    return sample


__all__ = ["parse_wg_transfer", "collect_and_publish_traffic"]
