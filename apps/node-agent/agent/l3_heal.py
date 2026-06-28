from __future__ import annotations

"""L3 漂移自愈——独立于 reconcile 的「观测-收敛」回路（与 :mod:`agent.reresolve` 同构）。

reconcile 是 **config_hash 门控**：desired-state 没变就判 KEEP、不动手。但「**期望的接口
在运行时被破坏**」是 config_hash 看不见的**运行时漂移**：

* WireGuard 接口被 ``ip link del`` / 内核 WG 异常拆除（隧道没了、容器仍 Up）；
* dns-anycast / 其它 dummy 接口在 netns 重建后未被补齐（外部重启绕过 agent convergence）；
* 任何「容器 Up，但 netns 里该在的接口不在」。

本回路周期性用 agent 的**直接探针**比对：**期望接口**（WireGuard + dummy，来自缓存
desired-state）vs **实际存在的接口**（``ip -br link``）。缺了就**就地重跑
``apply-all-wg.sh``**（幂等：``wg syncconf`` + ``ip addr/route replace`` + ``ip link add
dummy``）把缺失的补回来。

刻意做成独立回路而非塞进 reconcile：① 与 reresolve 一致、低风险、易测；② 纯运行时纠偏，
**绝不重建容器、不碰 ``applied_generation``、不触发对账**；③ 用直连探针（``ip`` / 后续可加
``wg``/``bird.ctl``）即时发现，不依赖 Docker healthcheck 的重建时序。
"""

import logging
import time

from dn42_schemas import DesiredState, InterfaceKind, ServiceRole

from .adapters import Adapters
from .core.config import AgentConfig
from .core.exec import ContainerExec
from .core.naming import service_container_by_role
from .core.paths import AgentPaths
from .desired_state.cache import load_cached_desired_state

logger = logging.getLogger(__name__)

_APPLY_ALL_WG = "/opt/dn42/scripts/wg/apply-all-wg.sh"
_APPLY_BIRD = "/opt/dn42/scripts/bird/apply-bird.sh"


def expected_interfaces(state: DesiredState) -> set[str]:
    """期望在 netns 里出现的受管接口名：全部 WireGuard + dummy（dn42-lo / dns-anycast …）。"""

    return {
        interface.name
        for interface in state.interfaces
        if interface.kind in (InterfaceKind.WIREGUARD, InterfaceKind.DUMMY)
    }


def parse_link_names(output: str) -> set[str]:
    """解析 ``ip -br link show`` 输出取接口名。

    每行形如 ``<name>[@<parent>] <FLAGS> <MAC> ...``；取首列、去掉 ``@parent`` 后缀。
    """

    names: set[str] = set()
    for line in output.splitlines():
        parts = line.split()
        if parts:
            names.add(parts[0].split("@", 1)[0])
    return names


def _present_interfaces(container_exec: ContainerExec, container: str) -> set[str] | None:
    """列出 netns 里实际存在的接口名；容器不可达 / 命令失败返回 ``None``。"""

    try:
        returncode, stdout, _stderr = container_exec.run(
            container, ["ip", "-br", "link", "show"]
        )
    except Exception:  # noqa: BLE001 - 容器不可达等统一降级
        return None
    if returncode != 0:
        return None
    return parse_link_names(stdout)


def _bird_alive(container_exec: ContainerExec, container: str) -> bool | None:
    """bird 控制 socket 是否应答（``birdc show status``）。

    ``True``=活；``False``=容器在、socket 不应答=**守护进程死在 Up 容器里**；
    ``None``=容器不可达（无从判断,跳过,避免误判）。这是「服务级存活」直连探针——
    取代依赖 Docker healthcheck 的重建时序（bird/WG 本就有控制 API）。
    """

    try:
        returncode, _stdout, _stderr = container_exec.run(
            container, ["birdc", "show", "status"]
        )
    except Exception:  # noqa: BLE001 - 容器不可达
        return None
    return returncode == 0


def heal_l3_drift(
    config: AgentConfig, adapters: Adapters, node_id: str, *, now: int | None = None
) -> dict | None:
    """检查一次本节点的运行时实况，纠正与期望的偏差。覆盖两类「容器 Up 但服务/L3 死」：

    1. **接口漂移**：期望接口（WG + dummy）在 netns 缺失 → 重跑 ``apply-all-wg`` 补齐；
    2. **bird 守护死**：``birdc`` 不应答（socket 拒连） → 重跑 ``apply-bird.sh`` 重启
       （脚本自带「死了就起、活着就 reconfigure」）。

    无漂移 / 探针均不可达 / 无缓存时返回 ``None``；有漂移则返回摘要（``healed`` 反映是否
    全部补好）。依赖缓存 desired-state，不打控制面、不重建容器、不碰 ``applied_generation``。
    """

    paths = AgentPaths(config.state_dir, node_id)
    state = load_cached_desired_state(paths.desired_state_file)
    if state is None:
        logger.debug("l3-heal: 无缓存 desired-state，跳过本轮")
        return None

    container_exec = adapters.container_exec
    summary: dict = {}
    healed = True

    # ---- ① 接口漂移（WG + dummy）----
    wg_container = service_container_by_role(state, ServiceRole.WG_GATEWAY)
    expected = expected_interfaces(state) if wg_container is not None else set()
    if wg_container is not None and expected:
        present = _present_interfaces(container_exec, wg_container)
        if present is None:
            logger.warning("l3-heal: 读取 netns 接口失败（容器不可达？），跳过接口检查")
        else:
            missing = sorted(expected - present)
            if missing:
                summary["missing_interfaces"] = missing
                logger.warning(
                    "l3-heal: 接口漂移，缺失 %s —— 重跑 %s 补齐", missing, _APPLY_ALL_WG
                )
                ok = _run(container_exec, wg_container, ["sh", _APPLY_ALL_WG], "apply-all-wg")
                after = _present_interfaces(container_exec, wg_container) or set()
                still = sorted(expected - after)
                ok = ok and not still
                summary["interfaces_healed"] = ok
                if ok:
                    logger.info("l3-heal: 接口漂移已补齐，缺失 %s 全部恢复", missing)
                else:
                    logger.warning("l3-heal: 接口补齐未完成，仍缺 %s", still)
                healed = healed and ok

    # ---- ② bird 守护存活 ----
    bird_container = service_container_by_role(state, ServiceRole.BIRD_ROUTER)
    if bird_container is not None:
        alive = _bird_alive(container_exec, bird_container)
        if alive is False:  # 容器在、socket 不应答 = bird 死
            summary["bird_dead"] = True
            logger.warning("l3-heal: bird 守护死在 Up 容器里（socket 不应答）—— 重跑 apply-bird.sh 重启")
            ok = _run(container_exec, bird_container, ["sh", _APPLY_BIRD], "apply-bird")
            ok = ok and _bird_alive(container_exec, bird_container) is True
            summary["bird_healed"] = ok
            if ok:
                logger.info("l3-heal: bird 已重启恢复")
            else:
                logger.warning("l3-heal: bird 重启未恢复")
            healed = healed and ok

    if not summary:
        return None  # 无漂移
    summary["healed"] = healed
    return summary


def _run(
    container_exec: ContainerExec, container: str, cmd: list[str], label: str
) -> bool:
    """在容器内跑一条补救命令；异常 / 非零退出均记日志返回 ``False``。"""

    try:
        returncode, _stdout, stderr = container_exec.run(container, cmd)
    except Exception:  # noqa: BLE001
        logger.warning("l3-heal: %s 执行异常", label, exc_info=True)
        return False
    if returncode != 0:
        logger.warning("l3-heal: %s rc=%s：%s", label, returncode, (stderr or "").strip())
        return False
    return True


class HealCircuit:
    """L3 自愈回路的退避 + 熔断状态机（纯逻辑，便于单测）。

    防止「故障持续（接口反复被删 / apply 一直补不好）→ 每轮无脑重 apply」打转/风暴：

    * **指数退避**：连续失败越多，下次重试间隔越长（``base × 2^failures``，封顶
      ``max_backoff``）；成功即回到 ``base``。
    * **熔断告警**：连续失败达 ``threshold`` 时返回 ``"escalate"``（让 loop 打一条
      需人工介入的告警，且只打一次）；之后仍按 max_backoff 慢速半开重试，底层好了能自愈。
    * **复位**：一次成功（补好 / 无漂移）即清零；若此前已熔断，返回 ``"recovered"``。

    刻意不直接停手而是退避到 max_backoff：既不风暴，又保留自恢复能力。
    """

    def __init__(
        self, base_interval: float, *, threshold: int = 3, max_backoff: float = 600.0
    ) -> None:
        self.base = base_interval
        self.threshold = threshold
        self.max_backoff = max_backoff
        self.failures = 0
        self._escalated = False

    def record(self, success: bool) -> str | None:
        """记录一次结果；返回 ``"escalate"``（刚熔断）/ ``"recovered"``（熔断后恢复）/ ``None``。"""

        if success:
            was_open = self._escalated
            self.failures = 0
            self._escalated = False
            return "recovered" if was_open else None
        self.failures += 1
        if self.failures >= self.threshold and not self._escalated:
            self._escalated = True
            return "escalate"
        return None

    def backoff(self) -> float:
        """下一轮等待秒数：无失败=base；有失败=指数退避封顶（指数也封顶防溢出）。"""

        if self.failures == 0:
            return self.base
        return min(self.base * (2 ** min(self.failures, 6)), self.max_backoff)


__all__ = [
    "expected_interfaces",
    "parse_link_names",
    "heal_l3_drift",
    "HealCircuit",
]
