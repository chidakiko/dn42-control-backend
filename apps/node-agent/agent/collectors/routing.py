from __future__ import annotations

"""BIRD 路由表全表的只读采集与解析（独立于 reconcile 的观测）。

设计与 :mod:`agent.collectors.network` 对齐：真正执行 ``birdc`` 的位置收敛到
注入式 ``command_runner``，解析逻辑纯函数化，单测直接喂样例输出，不碰真实
BIRD / Docker。

两步采集：

1. ``birdc show route ... all``——逐条路由（prefix / origin / as_path / next_hop
   / 来源 protocol / 是否最优）。BIRD 文本格式逐版本略有差异，解析只取跨版本
   稳定的字段，识别不到的项留空，绝不臆造。
2. （可选）``birdc show route table roa4/roa6``——ROA 表，本地按 RFC 6811 路由
   起源校验给每条路由打 ``valid`` / ``invalid`` / ``not-found``（三态）。ROA 采集失败
   时 rpki 全部留空（``None``，不参与统计；控制面据此标 ``rpki_observed=False``），
   不影响主路由采集。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from ipaddress import ip_network

from dn42_schemas import (
    ObservationStatus,
    ObservedRoute,
    PrefilterPeerStat,
    PrefilterRoute,
    PrefilterRpki,
    RoutingTableSnapshot,
)

# 上报的无效路由明细封顶,防极端情况下(对端灌大量非法路由)快照膨胀。
_MAX_INVALID_ROUTES = 200
# 被策略过滤器拒绝(非 RPKI 无效)的路由明细上限。前端要「显示全部 + 翻页」,故放宽到
# 能覆盖正常全量(单节点 reject 量级千条);保留一个高安全阀防对端恶意洪泛撑爆快照。
_MAX_FILTERED_ROUTES = 5000

from ..core.exec import ContainerExec
from ..core.naming import service_container_by_role
from dn42_schemas import DesiredState, ServiceRole

# runner 返回 None 表示采集失败；空串表示成功但无输出（与 network.py 同语义）。
CommandRunner = Callable[[], "str | None"]

# 路由块头行：可选前缀 + 路由类型 + [protocol ...]，行尾可能带 ``*``（最优）与
# ``[ASxxxxi]``（起源 AS）。附加路径（同前缀的次优路由）省略前缀、行首带缩进，
# 故前缀可选；``via`` / ``BGP.*`` 等属性行不含路由类型关键字，自然不匹配。
_ROUTE_HEADER = re.compile(
    r"^\s*"
    r"(?:(?P<prefix>[0-9a-fA-F:.]+/\d+)\s+)?"
    r"(?:unicast|blackhole|unreachable|prohibit)\s+"
    r"\[(?P<proto>[^\s\]]+)[^\]]*\]\s*"
    r"(?P<star>\*)?"
    r"[^\[]*"
    r"(?:\[AS(?P<origin>\d+)[ie?]?\])?"
)

# ROA 行：``prefix[-maxlen] ... AS<asn>``（容忍版本差异，按特征抓取三元组）。
_ROA_LINE = re.compile(
    r"(?P<net>[0-9a-fA-F:.]+/\d+)(?:-(?P<max>\d+))?\D+?(?:AS)?(?P<asn>\d+)\b"
)


@dataclass(slots=True)
class _RawRoute:
    """解析中间态：先攒齐字段，再连同 RPKI 结论构造不可变的 ObservedRoute。"""

    prefix: str
    protocol: str | None = None
    primary: bool = False
    origin_header: int | None = None
    next_hop: str | None = None
    as_path: list[int] = field(default_factory=list)
    communities: list[str] = field(default_factory=list)
    large_communities: list[str] = field(default_factory=list)

    @property
    def origin_asn(self) -> int | None:
        # AS path 末位是权威起源；为空（iBGP / 直连）时回退到头行的 [ASxxx]。
        if self.as_path:
            return self.as_path[-1]
        return self.origin_header


def parse_bird_routes(text: str) -> list[_RawRoute]:
    """解析 ``birdc show route all`` 输出为中间态路由列表。"""

    routes: list[_RawRoute] = []
    current: _RawRoute | None = None
    current_prefix: str | None = None

    for raw in text.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("Table "):  # "Table master4:" 分隔，跳过
            continue
        header = _ROUTE_HEADER.match(raw)
        if header:
            prefix = header.group("prefix") or current_prefix
            if prefix is None:
                # 次优路由出现在任何带前缀的头行之前——异常输出，跳过。
                continue
            current = _RawRoute(
                prefix=prefix,
                protocol=header.group("proto"),
                primary=bool(header.group("star")),
                origin_header=int(header.group("origin")) if header.group("origin") else None,
            )
            current_prefix = prefix
            routes.append(current)
            continue
        if current is None:
            continue
        stripped = raw.strip()
        if stripped.startswith("via ") and current.next_hop is None:
            parts = stripped.split()
            if len(parts) >= 2:
                current.next_hop = parts[1]
        elif stripped.startswith("BGP.as_path:"):
            current.as_path = [int(n) for n in re.findall(r"\d+", stripped[len("BGP.as_path:"):])]
        elif stripped.startswith("BGP.next_hop:") and current.next_hop is None:
            parts = stripped.split()
            if len(parts) >= 2:
                current.next_hop = parts[1]
        elif stripped.startswith("BGP.large_community:"):
            current.large_communities = [
                ":".join(parts) for parts in re.findall(r"\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", stripped)
            ]
        elif stripped.startswith("BGP.community:"):
            current.communities = [
                ":".join(parts) for parts in re.findall(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)", stripped)
            ]

    return routes


@dataclass(slots=True)
class RoaEntry:
    """单条 ROA：``network`` 允许起源 ``asn`` 宣告，最长到 ``max_len``。"""

    network: object  # IPv4Network | IPv6Network
    max_len: int
    asn: int


class RpkiIndex:
    """ROA 集合 + RFC 6811 路由起源校验。"""

    def __init__(self, entries: list[RoaEntry]) -> None:
        self._entries = entries

    @classmethod
    def from_bird(cls, text: str) -> "RpkiIndex":
        entries: list[RoaEntry] = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("Table "):
                continue
            match = _ROA_LINE.search(line)
            if not match:
                continue
            try:
                network = ip_network(match.group("net"), strict=False)
            except ValueError:
                continue
            max_raw = match.group("max")
            max_len = int(max_raw) if max_raw is not None else network.prefixlen
            entries.append(RoaEntry(network=network, max_len=max_len, asn=int(match.group("asn"))))
        return cls(entries)

    def classify(self, prefix: str, origin_asn: int | None) -> str | None:
        """RFC6811 三态:``valid`` / ``invalid`` / ``not-found``。

        无法判定（前缀解析失败、有 ROA 覆盖但拿不到起源 AS）返回 ``None``——
        **不参与统计**（这些极少见;ROA 表整张没采到时全 None,由上层标记不可用）。
        """

        try:
            net = ip_network(prefix, strict=False)
        except ValueError:
            return None
        covering = [
            entry
            for entry in self._entries
            if entry.network.version == net.version and net.subnet_of(entry.network)  # type: ignore[arg-type]
        ]
        if not covering:
            return "not-found"
        if origin_asn is None:
            return None
        for entry in covering:
            if entry.asn == origin_asn and net.prefixlen <= entry.max_len:
                return "valid"
        return "invalid"


def parse_ebgp_protocol_names(text: str) -> list[str]:
    """从 ``birdc show protocols`` 输出里取 eBGP 对端 protocol 名。

    只要类型为 BGP、且名字**不以 ``ibgp_`` 开头**（内部 iBGP 不配 import table）。
    """

    names: list[str] = []
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) < 2 or parts[0] in ("Name", "BIRD"):
            continue
        name, proto_type = parts[0], parts[1]
        if proto_type == "BGP" and not name.startswith("ibgp_"):
            names.append(name)
    return names


# DN42 合法前缀范围(与 config-bird2/bird.conf.j2 的 is_valid_network[_v6] 严格保持一致)。
# 每项 (网络, 最小长度, 最大长度);命中任一即 is_valid_network 为真。改模板这里要同步。
_DN42_VALID_V4 = [
    (ip_network("172.20.0.0/14"), 21, 29),
    (ip_network("172.20.0.0/24"), 28, 32),
    (ip_network("172.21.0.0/24"), 28, 32),
    (ip_network("172.22.0.0/24"), 28, 32),
    (ip_network("172.23.0.0/24"), 28, 32),
    (ip_network("172.31.0.0/16"), 16, 32),
    (ip_network("10.100.0.0/14"), 14, 32),
    (ip_network("10.0.0.0/8"), 15, 24),
    (ip_network("10.127.0.0/16"), 16, 32),
]
_DN42_VALID_V6 = [
    (ip_network("fd00::/8"), 44, 64),
    (ip_network("fd10:127::/32"), 32, 128),
]


@dataclass(slots=True)
class RejectPolicy:
    """本地复现 import 过滤器「非 RPKI」reject 判定所需的节点策略上下文。

    ``own_nets`` 是本节点自有前缀(is_self_net)；``rejected_asns`` 是拒收 ASN 集合
    (custom_filters.conf 的 ASES_REJECT)。均由 ``DesiredState`` 提供。
    """

    own_nets: list = field(default_factory=list)  # ip_network 列表(v4+v6)
    rejected_asns: frozenset = frozenset()


def _is_dn42_valid(net: object) -> bool:
    table = _DN42_VALID_V4 if net.version == 4 else _DN42_VALID_V6  # type: ignore[attr-defined]
    for base, lo, hi in table:
        if (
            net.version == base.version  # type: ignore[attr-defined]
            and net.subnet_of(base)  # type: ignore[arg-type]
            and lo <= net.prefixlen <= hi  # type: ignore[attr-defined]
        ):
            return True
    return False


def classify_reject_reason(
    prefix: str, as_path: list[int], policy: "RejectPolicy | None"
) -> str:
    """给一条被 import 过滤器拒绝(非 RPKI invalid)的路由判定**首要**原因。

    判定顺序与 community_filters.conf / custom_filters.conf 的 reject 分支一致：
    前缀越界 → self-net → AS path 过长 → 拒收 ASN → 其他策略兜底(``policy``)。
    """

    try:
        net = ip_network(prefix, strict=False)
    except ValueError:
        return "policy"
    if not _is_dn42_valid(net):
        return "out_of_range"
    if policy:
        for own in policy.own_nets:
            if own.version == net.version and net.subnet_of(own):
                return "self_net"
    if len(as_path) > 8:
        return "as_path_too_long"
    if policy and policy.rejected_asns and any(a in policy.rejected_asns for a in as_path):
        return "blocked_asn"
    return "policy"


def aggregate_prefilter(
    per_proto_routes: dict[str, list[_RawRoute]],
    accepted_by_proto: dict[str, int],
    index: "RpkiIndex | None",
    accepted_keys: "set[tuple[str, str]] | None" = None,
    policy: "RejectPolicy | None" = None,
) -> PrefilterRpki:
    """把每对端 import-table 路由（过滤前）按 RPKI 三态分类，聚合成 ``PrefilterRpki``。

    无法判定的路由（``classify`` 返回 ``None``，或 ``index`` 为 ``None`` 即 ROA 没采到）
    **不计入**任何桶。被拒最多（invalid+not_found）的对端排在 ``peers`` 前面。

    ``accepted_keys`` 是过滤后主表里出现的 ``(prefix, protocol)`` 集合：过滤前收到、
    却**不在**该集合里的路由即被 import 过滤器拒绝；其中 RPKI invalid 归 ``invalid_routes``，
    其余（bogon / 前缀长度 / AS path / community 等策略原因）归 ``filtered_routes``。
    传 ``None`` 时无从判定主表归属，``filtered_routes`` 留空（兼容旧调用）。
    """

    accepted_keys = accepted_keys if accepted_keys is not None else set()
    have_master = accepted_keys is not None and len(accepted_keys) > 0
    peers: list[PrefilterPeerStat] = []
    invalid_routes: list[PrefilterRoute] = []
    filtered_routes: list[PrefilterRoute] = []
    tot = {"received": 0, "accepted": 0, "valid": 0, "invalid": 0, "not_found": 0}
    for proto, routes in per_proto_routes.items():
        counts = {"valid": 0, "invalid": 0, "not_found": 0}
        asn_votes: dict[int, int] = {}
        for route in routes:
            verdict = index.classify(route.prefix, route.origin_asn) if index else None
            if verdict is not None:
                key = "not_found" if verdict == "not-found" else verdict
                counts[key] += 1
                if verdict == "invalid" and len(invalid_routes) < _MAX_INVALID_ROUTES:
                    invalid_routes.append(
                        PrefilterRoute(
                            prefix=route.prefix, origin_asn=route.origin_asn, protocol=proto
                        )
                    )
            # 被策略过滤器主动拒绝(没进主表)且非 RPKI 无效 ⇒ filtered_routes。
            # have_master 才判定:拿不到主表归属时不臆造"被拒"。
            if (
                have_master
                and verdict != "invalid"
                and (route.prefix, proto) not in accepted_keys
                and len(filtered_routes) < _MAX_FILTERED_ROUTES
            ):
                filtered_routes.append(
                    PrefilterRoute(
                        prefix=route.prefix,
                        origin_asn=route.origin_asn,
                        protocol=proto,
                        reason=classify_reject_reason(route.prefix, route.as_path, policy),
                    )
                )
            if route.as_path:  # eBGP：AS path 首位是对端 ASN
                asn_votes[route.as_path[0]] = asn_votes.get(route.as_path[0], 0) + 1
        remote_asn = max(asn_votes, key=lambda a: asn_votes[a]) if asn_votes else None
        peers.append(
            PrefilterPeerStat(
                protocol=proto,
                remote_asn=remote_asn,
                received=len(routes),
                accepted=accepted_by_proto.get(proto, 0),
                **counts,
            )
        )
        tot["received"] += len(routes)
        tot["accepted"] += accepted_by_proto.get(proto, 0)
        for k in ("valid", "invalid", "not_found"):
            tot[k] += counts[k]
    peers.sort(key=lambda p: (p.invalid + p.not_found, p.received), reverse=True)
    return PrefilterRpki(
        peers=peers,
        invalid_routes=invalid_routes,
        filtered_routes=filtered_routes,
        **tot,
    )


class RouteTableObserver:
    """采集并解析 BIRD 路由全表；可选叠加 RPKI 校验。"""

    def __init__(
        self,
        command_runner: CommandRunner | None = None,
        *,
        roa_runner: CommandRunner | None = None,
        protocols_runner: CommandRunner | None = None,
        import_table_runner: "Callable[[str, str], str | None] | None" = None,
    ) -> None:
        self._command_runner = command_runner
        self._roa_runner = roa_runner
        # 过滤前(import-table)采集用：列协议 + 按 (proto, channel) 取 import-table。
        self._protocols_runner = protocols_runner
        self._import_table_runner = import_table_runner
        # observe() 期间构建的 RPKI 索引，复用给 observe_prefilter()，避免二次取 ROA。
        self.index: RpkiIndex | None = None

    def observe(self) -> list[ObservedRoute] | None:
        if self._command_runner is None:
            return None
        output = self._command_runner()
        if output is None:
            return None
        raw_routes = parse_bird_routes(output)

        index: RpkiIndex | None = None
        if self._roa_runner is not None:
            roa_text = self._roa_runner()
            if roa_text is not None:
                index = RpkiIndex.from_bird(roa_text)
        self.index = index

        observed: list[ObservedRoute] = []
        for route in raw_routes:
            # 无 AS path ⇒ 本节点本地起源（static / direct / device）。本地路由
            # 只打标签、**不参与 RPKI**、也不改写起源：它们不对外宣告，对自有
            # ROA 做起源校验只会把 loopback /32 /128 这类更具体主机路由误判为
            # invalid（超 max-length），徒增噪音。外部学来的路由才做 RPKI。
            is_local = not route.as_path
            rpki = (
                None
                if is_local or index is None
                else index.classify(route.prefix, route.origin_asn)
            )
            observed.append(
                ObservedRoute(
                    prefix=route.prefix,
                    origin_asn=route.origin_asn,
                    as_path=route.as_path,
                    next_hop=route.next_hop,
                    protocol=route.protocol,
                    primary=route.primary,
                    local=is_local,
                    communities=route.communities,
                    large_communities=route.large_communities,
                    rpki=rpki,
                )
            )
        return observed

    def observe_prefilter(
        self, observed: list[ObservedRoute], policy: "RejectPolicy | None" = None
    ) -> PrefilterRpki | None:
        """采集每个 eBGP 对端的 import-table（过滤前），聚合过滤前 RPKI 分布。

        复用 ``observe()`` 期间构建的 ``self.index``；``observed``（过滤后主表）用来
        统计每对端 ``accepted`` 条数。任一前置 runner 缺失 / 协议清单取数失败 → None
        （前端不显示该区块，过滤后采集不受影响）。
        """

        if self._protocols_runner is None or self._import_table_runner is None:
            return None
        protocols_text = self._protocols_runner()
        if protocols_text is None:
            return None
        ebgp = parse_ebgp_protocol_names(protocols_text)
        if not ebgp:
            return None

        accepted_by_proto: dict[str, int] = {}
        # (prefix, protocol) ∈ 主表(过滤后) ⇒ 该路由通过了 import 过滤器。
        accepted_keys: set[tuple[str, str]] = set()
        for route in observed:
            if route.protocol:
                accepted_by_proto[route.protocol] = accepted_by_proto.get(route.protocol, 0) + 1
                accepted_keys.add((route.prefix, route.protocol))

        per_proto_routes: dict[str, list[_RawRoute]] = {}
        for proto in ebgp:
            collected: list[_RawRoute] = []
            for channel in ("ipv4", "ipv6"):
                text = self._import_table_runner(proto, channel)
                if text:
                    collected.extend(parse_bird_routes(text))
            if collected:
                per_proto_routes[proto] = collected
        if not per_proto_routes:
            return None
        return aggregate_prefilter(
            per_proto_routes, accepted_by_proto, self.index, accepted_keys, policy
        )


def _concat_runner(
    container_exec: ContainerExec, container: str, commands: list[list[str]]
) -> CommandRunner:
    """把多条容器内命令包装成一个 runner，拼接所有成功命令的输出。

    用于把 master4 / master6（或 roa4 / roa6）两张表合并采集：只要至少一条成功
    就返回拼接结果；全部失败才返回 ``None``（采集失败）。这样单栈 BIRD（只有
    master4）也能正常工作，缺失的表不拖垮整体。
    """

    def call() -> str | None:
        outputs: list[str] = []
        any_ok = False
        for argv in commands:
            try:
                returncode, stdout, _stderr = container_exec.run(container, argv)
            except Exception:  # noqa: BLE001 - 观察是 best-effort
                continue
            if returncode == 0:
                any_ok = True
                outputs.append(stdout)
        return "\n".join(outputs) if any_ok else None

    return call


def build_routing_observer(
    state: DesiredState, container_exec: ContainerExec
) -> RouteTableObserver | None:
    """构造生产路径的路由观察器（容器内 exec 进入 BIRD 采集全表 + ROA）。

    没有 BIRD 容器（全新节点 / 未部署）时返回 ``None``，调用方据此标记
    ``NOT_OBSERVED``，不产生假阳性。
    """

    bird_container = service_container_by_role(state, ServiceRole.BIRD_ROUTER)
    if bird_container is None:
        return None
    route_runner = _concat_runner(
        container_exec,
        bird_container,
        [
            ["birdc", "show", "route", "table", "master4", "all"],
            ["birdc", "show", "route", "table", "master6", "all"],
        ],
    )
    # ROA 表名与 BIRD 模板（config-bird2/bird.conf.j2）保持一致：
    # ``roa4 table dn42_roa;`` / ``roa6 table dn42_roa_v6;``。表名写错会导致
    # birdc 报错、整张 ROA 取数失败，所有路由 rpki 退化为 None（前端显示 —）。
    roa_runner = _concat_runner(
        container_exec,
        bird_container,
        [
            ["birdc", "show", "route", "table", "dn42_roa"],
            ["birdc", "show", "route", "table", "dn42_roa_v6"],
        ],
    )

    def protocols_runner() -> str | None:
        try:
            rc, out, _ = container_exec.run(bird_container, ["birdc", "show", "protocols"])
        except Exception:  # noqa: BLE001 - 观察 best-effort
            return None
        return out if rc == 0 else None

    def import_table_runner(proto: str, channel: str) -> str | None:
        # BIRD2:`show route import table <proto>.<channel> all`(channel=ipv4/ipv6)。
        try:
            rc, out, _ = container_exec.run(
                bird_container,
                ["birdc", "show", "route", "import", "table", f"{proto}.{channel}", "all"],
            )
        except Exception:  # noqa: BLE001
            return None
        return out if rc == 0 else None

    return RouteTableObserver(
        route_runner,
        roa_runner=roa_runner,
        protocols_runner=protocols_runner,
        import_table_runner=import_table_runner,
    )


def collect_routing_snapshot(
    state: DesiredState,
    container_exec: ContainerExec,
    *,
    captured_at: str,
) -> RoutingTableSnapshot:
    """采集一次路由全表，组装成 ``RoutingTableSnapshot``（含三态观测语义）。"""

    node_id = state.node.node_id
    observer = build_routing_observer(state, container_exec)
    if observer is None:
        return RoutingTableSnapshot(
            node_id=node_id,
            captured_at=captured_at,
            observation=ObservationStatus.NOT_OBSERVED,
        )
    routes = observer.observe()
    if routes is None:
        return RoutingTableSnapshot(
            node_id=node_id,
            captured_at=captured_at,
            observation=ObservationStatus.UNAVAILABLE,
        )
    # 过滤前(import-table)分布：best-effort，失败为 None,不影响过滤后采集。
    # policy 提供本节点自有网段 + 拒收 ASN,供给被策略过滤的路由标注首要原因。
    own_nets: list = []
    for prefix in list(state.node.ipv4_prefixes) + list(state.node.ipv6_prefixes):
        try:
            own_nets.append(ip_network(prefix, strict=False))
        except ValueError:
            continue
    policy = RejectPolicy(
        own_nets=own_nets,
        rejected_asns=frozenset(state.bird.large_communities.rejected_asns),
    )
    try:
        prefilter = observer.observe_prefilter(routes, policy)
    except Exception:  # noqa: BLE001
        prefilter = None
    return RoutingTableSnapshot(
        node_id=node_id,
        captured_at=captured_at,
        observation=ObservationStatus.OBSERVED,
        routes=routes,
        prefilter=prefilter,
    )


__all__ = [
    "CommandRunner",
    "RejectPolicy",
    "RoaEntry",
    "RouteTableObserver",
    "RpkiIndex",
    "aggregate_prefilter",
    "classify_reject_reason",
    "build_routing_observer",
    "collect_routing_snapshot",
    "parse_bird_routes",
    "parse_ebgp_protocol_names",
]
