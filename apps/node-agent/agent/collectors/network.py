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

import time
from typing import Callable

from dn42_schemas import (
    ObservedBgpProtocol,
    ObservedWireGuardInterface,
    ObservedWireGuardPeer,
    RuntimeResourceStatus,
)

# runner 返回 None 表示采集失败；空串表示成功但无输出。
CommandRunner = Callable[[], "str | None"]


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


class WireGuardObserver:
    """解析 `wg show all dump` 输出为 ObservedWireGuardInterface 列表（含 per-peer 隧道状态）。

    dump 格式按接口分组：每个接口的第一行 5 列
    （interface / private-key / public-key / listen-port / fwmark），其后每个 peer
    一行 9 列（interface / public-key / preshared-key / endpoint / allowed-ips /
    latest-handshake / transfer-rx / transfer-tx / persistent-keepalive）。接口名 /
    监听端口 / peer 数量是跨发行版稳定的；peer 行进一步取 endpoint / 最近握手 / 收发
    字节，构成隧道存活监控的原始事实（up/stale/down 判定留给消费端）。
    """

    def __init__(self, command_runner: CommandRunner | None = None) -> None:
        self._command_runner = command_runner

    def observe(self, *, now: float | None = None) -> list[ObservedWireGuardInterface] | None:
        if self._command_runner is None:
            return None
        output = self._command_runner()
        if output is None:
            return None
        return self._parse(output, now=time.time() if now is None else now)

    @staticmethod
    def _parse(output: str, *, now: float) -> list[ObservedWireGuardInterface]:
        listen_ports: dict[str, int | None] = {}
        peers: dict[str, list[ObservedWireGuardPeer]] = {}
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
                peers[interface] = []
            if len(fields) == 5:
                # 接口自身行：interface privkey pubkey listen-port fwmark
                port = _safe_int(fields[3].strip())
                # wg dump 用 0 表示未设置监听端口；schema 要求 >=1，归一为 None。
                listen_ports[interface] = port if port else None
            elif len(fields) >= 8:
                # peer 行：iface pubkey psk endpoint allowed-ips handshake rx tx [keepalive]
                peers[interface].append(WireGuardObserver._parse_peer(fields, now=now))

        return [
            ObservedWireGuardInterface(
                name=name,
                listen_port=listen_ports[name],
                peer_count=len(peers[name]),
                status=RuntimeResourceStatus.RUNNING,
                peers=peers[name],
            )
            for name in order
        ]

    @staticmethod
    def _parse_peer(fields: list[str], *, now: float) -> ObservedWireGuardPeer:
        endpoint = fields[3].strip()
        handshake_epoch = _safe_int(fields[5].strip())
        # latest-handshake=0 表示从未握手；否则用采集时刻减去握手时间得"距今秒数"（不为负）。
        age = (
            max(0, int(now) - handshake_epoch)
            if handshake_epoch is not None and handshake_epoch > 0
            else None
        )
        return ObservedWireGuardPeer(
            public_key=fields[1].strip(),
            endpoint=None if endpoint in ("", "(none)") else endpoint,
            last_handshake_seconds=age,
            transfer_rx_bytes=_safe_int(fields[6].strip()) or 0,
            transfer_tx_bytes=_safe_int(fields[7].strip()) or 0,
        )


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
