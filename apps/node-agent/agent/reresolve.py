from __future__ import annotations

"""WG endpoint 周期性重解析——**独立于 reconcile** 的自愈观测路径。

WireGuard 的 ``Endpoint`` 域名只在 ``wg syncconf`` 那一刻解析一次，内核随后钉死
解析出的 IP。对端走动态 DNS、IP 变更后我方不会自动刷新（agent 仅在该接口
``config_hash`` 变化时才重跑 apply 脚本），隧道会静默失联。

本模块据 wireguard-tools 的 ``contrib/reresolve-dns`` 思路做自愈：周期性检查每个
「域名 endpoint」对端的最近握手，超过 :data:`STALE_HANDSHAKE_SECONDS` 即用配置里
的域名重设 endpoint（``wg set ... endpoint``），让内核重新解析。只在**确有重设**时
上报控制面（``kind=reresolve`` 事件），无 stale 对端时只在本地记日志、不打扰控制
面。绝不参与对账 / apply，不触碰 ``applied_generation``。

刻意只动「域名 endpoint」：静态 IP endpoint 的 host 不会因 DNS 变化，重设无意义；
endpoint 缺省（被动等对端来连）无从解析。两者都跳过。``wg set ... endpoint`` 对已
建立的隧道是幂等热更新（解析到同一 IP 即无扰动），故对暂时不可达的对端反复重设
也无副作用。
"""

import logging
import time
from dataclasses import dataclass
from ipaddress import ip_address

from dn42_schemas import (
    DesiredState,
    InterfaceKind,
    ServiceRole,
    WireGuardReresolveEntry,
    WireGuardReresolveReport,
)

from .adapters import Adapters
from .core.clock import utc_now_iso
from .core.config import AgentConfig
from .core.exec import ContainerExec
from .core.naming import service_container_by_role
from .core.paths import AgentPaths
from .desired_state.cache import load_cached_desired_state

logger = logging.getLogger(__name__)

# 与 wireguard-tools reresolve-dns 取同一阈值：握手周期 ~120s + REKEY 余量。
STALE_HANDSHAKE_SECONDS = 135

_ENDPOINT_NONE = "(none)"


@dataclass(frozen=True)
class ReresolveTarget:
    """一个待检查重解析的 WG 对端（其 endpoint 为域名）。"""

    interface: str
    public_key: str
    endpoint: str


def _endpoint_host(endpoint: str) -> str | None:
    """从 ``host:port`` / ``[v6]:port`` 取出 host；取不到返回 ``None``。"""

    ep = endpoint.strip()
    if not ep:
        return None
    if ep.startswith("["):  # [2001:db8::1]:51820
        close = ep.find("]")
        return ep[1:close] if close > 1 else None
    # host:port —— IPv4 / 域名的 host 不含冒号。无端口也容忍。
    return ep.rsplit(":", 1)[0] if ":" in ep else ep


def _is_hostname_endpoint(endpoint: str) -> bool:
    """endpoint 的 host 是域名（需 DNS 解析）才 ``True``；IP 字面量 ``False``。"""

    host = _endpoint_host(endpoint)
    if not host:
        return False
    try:
        ip_address(host)
    except ValueError:
        return True  # 不是 IP 字面量 = 域名
    return False


def _clean_endpoint(value: str | None) -> str | None:
    """把 ``wg show`` 的 ``(none)`` / 空串归一成 ``None``，其余原样返回。"""

    if not value or value == _ENDPOINT_NONE:
        return None
    return value


def collect_reresolve_targets(state: DesiredState) -> list[ReresolveTarget]:
    """从 desired-state 选出「域名 endpoint」的 WG 对端——只有它们需要重解析。"""

    targets: list[ReresolveTarget] = []
    for iface in state.interfaces:
        if iface.kind != InterfaceKind.WIREGUARD:
            continue
        peer = iface.wireguard_peer
        if peer is None or not peer.endpoint:
            continue
        if not _is_hostname_endpoint(peer.endpoint):
            continue
        targets.append(
            ReresolveTarget(
                interface=iface.name,
                public_key=peer.public_key,
                endpoint=peer.endpoint,
            )
        )
    return targets


def parse_wg_pairs(output: str) -> dict[tuple[str, str], str]:
    """解析 ``wg show all latest-handshakes|endpoints`` 的三列输出。

    每行形如 ``<interface>\\t<public_key>\\t<value>``；返回 ``{(if, pubkey): value}``。
    字段无内部空白（接口名 / base64 公钥 / ``ip:port`` / 整数 / ``(none)``），故按
    任意空白切分即安全。
    """

    result: dict[tuple[str, str], str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        result[(parts[0], parts[1])] = parts[2]
    return result


def select_stale(
    targets: list[ReresolveTarget],
    handshakes: dict[tuple[str, str], str],
    now: int,
    threshold: int = STALE_HANDSHAKE_SECONDS,
) -> list[tuple[ReresolveTarget, int | None]]:
    """挑出握手超时或从未握手的域名对端，返回 ``(target, stale_seconds)``。

    - 对端不在活动接口上（``handshakes`` 无此键）→ 跳过：交给 reconcile 落地接口，
      reresolve 绝不经 ``wg set`` 新增 peer（会得到一个缺 allowed-ips 的残缺 peer）。
    - 握手 epoch 为 ``0`` / 非数字 → 从未握手 → 纳入（``stale_seconds=None``）。
    - 否则 ``now - hs > threshold`` 才纳入。
    """

    stale: list[tuple[ReresolveTarget, int | None]] = []
    for target in targets:
        raw = handshakes.get((target.interface, target.public_key))
        if raw is None:
            continue
        hs = int(raw) if raw.isdigit() else 0
        if hs == 0:
            stale.append((target, None))
            continue
        age = now - hs
        if age > threshold:
            stale.append((target, age))
    return stale


def _wg_pairs(
    container_exec: ContainerExec, container: str, what: str, errors: list[str]
) -> dict[tuple[str, str], str] | None:
    """跑 ``wg show all <what>`` 并解析；失败记 error 返回 ``None``。"""

    try:
        returncode, stdout, stderr = container_exec.run(container, ["wg", "show", "all", what])
    except Exception as exc:  # noqa: BLE001 - 容器不可达等统一降级
        errors.append(f"wg show all {what}: {exc}")
        return None
    if returncode != 0:
        errors.append(f"wg show all {what}: rc={returncode} {stderr.strip()}")
        return None
    return parse_wg_pairs(stdout)


def reresolve_and_report(
    config: AgentConfig, adapters: Adapters, node_id: str, *, now: int | None = None
) -> WireGuardReresolveReport | None:
    """检查一次本节点全部「域名 endpoint」对端，重设 stale 者并按需上报。

    返回本轮报告（无缓存 / 无 wg 容器 / 无域名对端 / 无 stale 时返回 ``None``）。
    依赖 reconcile 落盘的缓存 desired-state 拿对端清单与 wg-gateway 容器名，不打控制面。
    """

    paths = AgentPaths(config.state_dir, node_id)
    state = load_cached_desired_state(paths.desired_state_file)
    if state is None:
        logger.debug("reresolve: 无缓存 desired-state，跳过本轮")
        return None
    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    if wg_container is None:
        logger.debug("reresolve: desired-state 无 wg-gateway 容器，跳过本轮")
        return None
    targets = collect_reresolve_targets(state)
    if not targets:
        logger.debug("reresolve: 无「域名 endpoint」对端，跳过本轮")
        return None

    container_exec = adapters.container_exec
    errors: list[str] = []
    handshakes = _wg_pairs(container_exec, wg_container, "latest-handshakes", errors)
    if handshakes is None:
        logger.warning("reresolve: 读取 wg 握手失败（容器不可达？），跳过本轮：%s", errors)
        return None

    if now is None:
        now = int(time.time())
    stale = select_stale(targets, handshakes, now)
    if not stale:
        logger.debug("reresolve: %d 个域名对端握手均新鲜，无需重设", len(targets))
        return None

    before_ep = _wg_pairs(container_exec, wg_container, "endpoints", errors) or {}
    reset_ok: list[tuple[ReresolveTarget, int | None]] = []
    for target, age in stale:
        try:
            returncode, _stdout, stderr = container_exec.run(
                wg_container,
                ["wg", "set", target.interface, "peer", target.public_key,
                 "endpoint", target.endpoint],
            )
        except Exception as exc:  # noqa: BLE001 - 单个重设失败不影响其余
            errors.append(f"wg set {target.interface}: {exc}")
            logger.warning("reresolve: 重设 %s 异常：%s", target.interface, exc)
            continue
        if returncode == 0:
            reset_ok.append((target, age))
        else:
            errors.append(f"wg set {target.interface}: rc={returncode} {stderr.strip()}")
            logger.warning("reresolve: 重设 %s 失败 rc=%s：%s", target.interface, returncode, stderr.strip())

    after_ep = _wg_pairs(container_exec, wg_container, "endpoints", errors) or {}
    entries = [
        WireGuardReresolveEntry(
            interface=target.interface,
            public_key=target.public_key,
            endpoint=target.endpoint,
            previous_endpoint=_clean_endpoint(before_ep.get((target.interface, target.public_key))),
            resolved_endpoint=_clean_endpoint(after_ep.get((target.interface, target.public_key))),
            stale_seconds=age,
        )
        for target, age in reset_ok
    ]
    for entry in entries:
        logger.info(
            "reresolve: %s peer=%s endpoint=%s %s->%s (stale=%ss)",
            entry.interface,
            entry.public_key[:8],
            entry.endpoint,
            entry.previous_endpoint,
            entry.resolved_endpoint,
            entry.stale_seconds,
        )
    report = WireGuardReresolveReport(
        node_id=node_id,
        captured_at=utc_now_iso(),
        checked=len(targets),
        reresolved=entries,
        errors=errors,
    )
    if entries and adapters.session is not None:
        try:
            adapters.session.call(lambda client: client.post_wireguard_reresolve(report))
        except Exception:  # noqa: BLE001 - 上报 best-effort（含旧控制面 404）
            logger.warning("reresolve: 上报控制面失败（忽略，不影响自愈）", exc_info=True)
    return report


__all__ = [
    "STALE_HANDSHAKE_SECONDS",
    "ReresolveTarget",
    "collect_reresolve_targets",
    "parse_wg_pairs",
    "select_stale",
    "reresolve_and_report",
]
