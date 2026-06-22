from __future__ import annotations

"""WireGuard / BGP 侧的只读观察工具。

设计与 `DockerObserver` 对齐：把真正执行外部命令（`wg show all dump`、
`birdc show protocols`）的位置收敛到注入式的 `command_runner`，方便测试里直接喂
样例输出。

``observe()`` 返回 ``list | None``，三态区分清楚：

- ``None``：未注入 runner，或 runner 报告采集失败（命令非零 / 容器不可达）——
  状态未知，下游标为 unavailable，绝不当作"无 drift"；
- ``list``（可能为空）：采集成功，结果权威，空列表即"真的没有"。
"""

from typing import Callable

from dn42_schemas import ObservedBgpProtocol, ObservedWireGuardInterface, RuntimeResourceStatus

# runner 返回 None 表示采集失败；空串表示成功但无输出。
CommandRunner = Callable[[], "str | None"]


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


class WireGuardObserver:
    """解析 `wg show all dump` 输出为 ObservedWireGuardInterface 列表。

    dump 格式按接口分组：每个接口的第一行 5 列
    （interface / private-key / public-key / listen-port / fwmark），其后每个 peer
    一行 9 列。我们只取跨发行版稳定的三件事：接口名 / 监听端口 / peer 数量。
    """

    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self._command_runner = command_runner

    def observe(self) -> list[ObservedWireGuardInterface] | None:
        if self._command_runner is None:
            return None
        output = self._command_runner()
        if output is None:
            return None
        return self._parse(output)

    @staticmethod
    def _parse(output: str) -> list[ObservedWireGuardInterface]:
        listen_ports: dict[str, int | None] = {}
        peer_counts: dict[str, int] = {}
        order: list[str] = []
        for raw in output.splitlines():
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            fields = line.split("\t")
            interface = fields[0].strip()
            if not interface:
                continue
            if interface not in listen_ports:
                order.append(interface)
                listen_ports[interface] = None
                peer_counts[interface] = 0
            if len(fields) == 5:
                # 接口自身行：interface privkey pubkey listen-port fwmark
                port = _safe_int(fields[3].strip())
                # wg dump 用 0 表示未设置监听端口；schema 要求 >=1，归一为 None。
                listen_ports[interface] = port if port else None
            else:
                # peer 行
                peer_counts[interface] += 1

        return [
            ObservedWireGuardInterface(
                name=name,
                listen_port=listen_ports[name],
                peer_count=peer_counts[name],
                status=RuntimeResourceStatus.RUNNING,
            )
            for name in order
        ]


class BgpObserver:
    """解析 `birdc show protocols` 输出为 ObservedBgpProtocol 列表。

    只保留 `Proto == BGP` 的行。`state` 取 BGP 的 Info 关键字（如 `Established` /
    `Active`）；因为 Since 列宽不固定（可能含日期+时间两段），用已知的 BGP info
    关键字集合从行尾反向识别 Info，识别不到时回退到 State 列（`up` / `down`）。
    `session` 由可选的 `name_to_session` 映射反查，缺失时退化为协议名本身。
    """

    _BGP_INFO_STATES = frozenset(
        {
            "established",
            "active",
            "connect",
            "opensent",
            "openconfirm",
            "idle",
            "passive",
            "close",
        }
    )

    def __init__(
        self,
        command_runner: CommandRunner | None = None,
        *,
        name_to_session: dict[str, str] | None = None,
    ) -> None:
        self._command_runner = command_runner
        self._name_to_session = name_to_session or {}

    def observe(self) -> list[ObservedBgpProtocol] | None:
        if self._command_runner is None:
            return None
        output = self._command_runner()
        if output is None:
            return None
        return self._parse(output)

    def _parse(self, output: str) -> list[ObservedBgpProtocol]:
        protocols: list[ObservedBgpProtocol] = []
        for raw in output.splitlines():
            line = raw.rstrip("\n")
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) < 4:
                continue
            name, proto, _table, state = fields[0], fields[1], fields[2], fields[3]
            if proto.upper() != "BGP":
                continue
            info = self._extract_info(fields[4:])
            bgp_state = info if info else state
            protocols.append(
                ObservedBgpProtocol(
                    name=name,
                    session=self._name_to_session.get(name, name),
                    state=bgp_state,
                    info=info,
                )
            )
        return protocols

    @classmethod
    def _extract_info(cls, tail_fields: list[str]) -> str | None:
        for token in reversed(tail_fields):
            if token.lower() in cls._BGP_INFO_STATES:
                return token
        return None


__all__ = ["BgpObserver", "WireGuardObserver", "CommandRunner"]
