from __future__ import annotations

"""BIRD2 配置模板的上下文构造与渲染入口。"""

from dataclasses import dataclass
from ipaddress import ip_address
import json
import math
from pathlib import Path
import re
from typing import Any

from dn42_common import Dn42OriginRegionCommunity
from dn42_schemas import AddressFamily, DesiredState, InterfaceKind, ServiceRole, resolve_service_ipv4
from jinja2 import Environment, FileSystemLoader

from .paths import config_bird2_template_dir


CONFIG_BIRD2_TEMPLATE_NAMES = [
    "community_filters.conf",
    "custom_filters.conf",
    "rpki.conf",
    "anycast_services.conf",
    "bird.conf",
    "dn42_peers.conf",
    "ibgp.conf",
    "ospf.conf",
    "ospf_interfaces.conf",
]


@dataclass(frozen=True, slots=True)
class Bird2RenderedFile:
    """单个渲染后的 BIRD 配置文件。"""

    path: str
    content: str


def create_config_bird2_environment(template_dir: str | Path | None = None) -> Environment:
    """创建用于渲染 BIRD2 模板的 Jinja2 环境。

    这里同时注册模板所依赖的过滤器与全局函数，
    因此外部若要做单模板渲染测试，应该优先复用这个环境创建函数。
    """

    env = Environment(
        loader=FileSystemLoader(str(template_dir or config_bird2_template_dir())),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["get_dn42_remote_ip"] = _get_dn42_remote_ip
    env.filters["log"] = _jinja_log
    env.filters["to_json"] = json.dumps
    return env


def render_config_bird2_template(
    name: str,
    context: dict[str, Any],
    env: Environment | None = None,
) -> str:
    """渲染单个 BIRD2 模板文件。"""

    active_env = env or create_config_bird2_environment()
    template_name = _resolve_template_name(name, active_env)
    return active_env.get_template(template_name).render(**context)


def render_config_bird2_set(
    context: dict[str, Any],
    env: Environment | None = None,
    names: list[str] | None = None,
) -> list[Bird2RenderedFile]:
    """渲染一组 BIRD2 配置模板并返回路径加内容的文件列表。"""

    active_env = env or create_config_bird2_environment()
    return [
        Bird2RenderedFile(
            _rendered_config_name(name),
            render_config_bird2_template(name, context, active_env),
        )
        for name in (names or CONFIG_BIRD2_TEMPLATE_NAMES)
    ]


def build_config_bird2_context(
    state: DesiredState,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把 `DesiredState` 翻译成 BIRD2 模板上下文。

    这是模板层最核心的上下文构造入口。它会把：

    - 节点自身前缀与 loopback
    - internal topology 派生出的 iBGP / OSPF 信息
    - 显式 BGP session
    - large community 相关字段
    - RPKI 服务地址

    统一整理成现有 BIRD 模板可直接消费的字典结构。
    """

    bird = state.bird
    topology = bird.internal_topology
    if topology:
        bird_hosts = {name: value.model_dump(mode="json") for name, value in topology.hosts.items()}
        internal_router_names = topology.routers
        private_router_names = topology.private_nodes
    else:
        bird_hosts = {
            state.node.node_id: {
                "ownip": state.node.loopback_ipv4 or state.node.router_id,
                "ownip6": state.node.loopback_ipv6 or "::1",
                "ibgp_rr_upstreams": [],
            }
        }
        internal_router_names = [state.node.node_id]
        private_router_names = []
    rpki_service = next(
        (service for service in state.runtime.services if service.role == ServiceRole.RPKI_CACHE and service.enabled),
        None,
    )
    rpki_ip = resolve_service_ipv4(state.runtime, rpki_service) if rpki_service else state.runtime.rpki.listen_host

    context: dict[str, Any] = {
        "automation_root_dir": "/opt/dn42",
        "disable_ebgp": bird.disable_ebgp,
        "dn42_ratelimit": bird.dn42_ratelimit,
        "dn42_import_limit": bird.import_limit,
        "dn42_import_limit_action": bird.import_limit_action,
        "dn42_region": int(_default_region(state)),
        "export_ownnets": bird.export_ownnets,
        "bird_hostname": _bird_hostname(state),
        "node_id": state.node.node_id,
        "internal_router_names": internal_router_names,
        "private_router_names": private_router_names,
        "bird_hosts": bird_hosts,
        "ibgp_nets4_ipset": _bird_ipset(_ibgp_prefixes(state, 4), more_specific=True),
        "ibgp_nets6_ipset": _bird_ipset(_ibgp_prefixes(state, 6), more_specific=True),
        "ospf_neighbor_interfaces": _ospf_neighbor_interfaces(state),
        "stub_interface_names": _stub_interface_names(state),
        "anycast_interface_names": _anycast_interface_names(state),
        "large_communities": _large_community_context(state),
        "ownas": state.node.asn,
        "ownip": state.node.loopback_ipv4 or state.node.router_id,
        "ownip6": state.node.loopback_ipv6 or "::1",
        "ownnets4": sorted(state.node.ipv4_prefixes),
        "ownnets6": sorted(state.node.ipv6_prefixes),
        "ownnets4_aggr_ipset": _bird_ipset(state.node.ipv4_prefixes),
        "ownnets6_aggr_ipset": _bird_ipset(state.node.ipv6_prefixes),
        "ownnets4_ipset": _bird_ipset(state.node.ipv4_prefixes, more_specific=True),
        "ownnets6_ipset": _bird_ipset(state.node.ipv6_prefixes, more_specific=True),
        "region": _default_region(state),
        "rpki_ip": rpki_ip,
        "static_routes4": bird.static_routes4,
        "static_routes6": bird.static_routes6,
        "wg_peers": _wireguard_peer_contexts(state),
        "gre_peers": [],
        "route_collectors": _route_collector_contexts(state),
    }
    ibgp_rr_upstreams = _ibgp_rr_upstreams(state)
    if ibgp_rr_upstreams is not None:
        context["ibgp_rr_upstreams"] = ibgp_rr_upstreams
    if extra_context:
        context.update(extra_context)
    return context


def _resolve_template_name(name: str, env: Environment) -> str:
    if env.loader is None:
        return name
    try:
        env.loader.get_source(env, name)
        return name
    except Exception:
        if name.endswith(".j2"):
            raise
    return f"{name}.j2"


def _rendered_config_name(name: str) -> str:
    return name.removesuffix(".j2")


def _bird_ipset(prefixes: list[str], more_specific: bool = False) -> str:
    suffix = "+" if more_specific else ""
    return ", ".join(f"{prefix}{suffix}" for prefix in sorted(prefixes))


def _default_region(state: DesiredState) -> Dn42OriginRegionCommunity:
    region = state.bird.region or state.node.region
    return region


def _bird_hostname(state: DesiredState) -> str:
    if state.bird.internal_topology:
        return state.node.node_id
    return f"{state.node.node_id}-int"


def _large_community_context(state: DesiredState) -> dict[str, Any]:
    community = state.bird.large_communities
    rejected_asns = sorted(set(community.rejected_asns)) or [0]
    return {
        "origin_node_type": community.origin_node_type,
        "origin_region_type": community.origin_region_type,
        "policy_type": community.policy_type,
        "origin_node_id": community.origin_node_id or _derive_origin_node_id(state),
        "policy_local_pref": community.policy_local_pref,
        "policy_deprep": community.policy_deprep,
        "rejected_asns": rejected_asns,
    }


def _derive_origin_node_id(state: DesiredState) -> int:
    address = ip_address(state.node.loopback_ipv4 or state.node.router_id)
    return int(address) & 0xFFFF


def _ibgp_prefixes(state: DesiredState, version: int) -> list[str]:
    prefixes = list(state.node.ipv4_prefixes if version == 4 else state.node.ipv6_prefixes)
    loopback = state.node.loopback_ipv4 if version == 4 else state.node.loopback_ipv6
    if loopback:
        prefix_length = 32 if version == 4 else 128
        prefixes.append(f"{loopback}/{prefix_length}")
    return prefixes


def _ospf_neighbor_interfaces(state: DesiredState) -> list[dict[str, Any]]:
    topology = state.bird.internal_topology
    if not topology:
        return []
    return sorted(
        [
            {
                "name": adjacency.interface or f"igp-{adjacency.node}",
                "peer_node": adjacency.node,
                "cost": adjacency.cost,
                "iface_type": adjacency.iface_type,
            }
            for adjacency in topology.igp_adjacencies
        ],
        key=lambda item: item["name"],
    )


def _stub_interface_names(state: DesiredState) -> list[str]:
    bird = state.bird
    names = [*bird.stub_ifnames, *bird.stub_ifnames_append]
    names.extend(spec.ifname for spec in bird.dummy_interfaces.values() if not spec.track_service)
    return sorted(dict.fromkeys(names))


def _anycast_interface_names(state: DesiredState) -> list[str]:
    return sorted(spec.ifname for spec in state.bird.dummy_interfaces.values() if spec.track_service)


def _ibgp_rr_upstreams(state: DesiredState) -> list[str] | None:
    topology = state.bird.internal_topology
    if not topology:
        return None
    current = topology.hosts.get(state.node.node_id)
    if current and current.ibgp_rr_upstreams:
        return current.ibgp_rr_upstreams
    return None


def _wireguard_peer_contexts(state: DesiredState) -> list[dict[str, Any]]:
    sessions_by_interface: dict[str, list[Any]] = {}
    for session in state.bgp_sessions:
        if (
            session.enabled
            and session.interface
            and session.remote_asn != state.node.asn
            and session.policy != "internal"
        ):
            sessions_by_interface.setdefault(session.interface, []).append(session)

    peers: list[dict[str, Any]] = []
    for interface in sorted(state.interfaces, key=lambda item: item.name):
        if interface.kind != InterfaceKind.WIREGUARD:
            continue
        sessions = sessions_by_interface.get(interface.name, [])
        if not sessions:
            continue
        asns = {session.remote_asn for session in sessions}
        peer_v4 = _session_neighbor(sessions, AddressFamily.IPV4)
        peer_v6 = _session_neighbor(sessions, AddressFamily.IPV6)
        peer = interface.wireguard_peer
        peers.append(
            {
                "name": interface.name,
                "remote": bool(peer and peer.endpoint),
                "peer_v4": peer_v4,
                "peer_v6": peer_v6,
                "bgp": {
                    "asn": asns.pop(),
                    "ipv4": peer_v4 is not None,
                    "ipv6": peer_v6 is not None,
                    "extended_next_hop": any(
                        session.extended_next_hop for session in sessions
                    ),
                    "import_mode": _first_session_value(sessions, "import_mode"),
                    "export_mode": _first_session_value(sessions, "export_mode"),
                    "import_limit": _first_session_value(sessions, "import_limit"),
                    "import_limit_action": _first_session_value(sessions, "import_limit_action"),
                    "mp_bgp": any(
                        session.address_family == AddressFamily.MP_BGP for session in sessions
                    ),
                    "opts": _session_options(sessions),
                    "suffix": _first_session_value(sessions, "protocol_suffix"),
                    "protocol_v4": _session_protocol_name(sessions, AddressFamily.IPV4),
                    "protocol_v6": _session_protocol_name(sessions, AddressFamily.IPV6),
                    "protocol_mp": _session_protocol_name(sessions, AddressFamily.MP_BGP),
                },
            }
        )
    return peers


def _route_collector_contexts(state: DesiredState) -> list[dict[str, Any]]:
    """收集 ``policy=route_collector`` 的多跳收集器喂送会话（无 WG 接口）。

    这类会话不挂在隧道接口上：经 DN42 网络**多跳**直连收集器（如 Kioubit GRC），
    单向把全量路由导出给收集器（``import none``），用现成的 ``route_collector``
    模板渲染。因 ``interface`` 为空，``_wireguard_peer_contexts`` 天然不收它们，
    故两条渲染路径互不重叠。
    """

    collectors: list[dict[str, Any]] = []
    for session in sorted(state.bgp_sessions, key=lambda item: item.name):
        if not session.enabled or session.policy != "route_collector":
            continue
        collectors.append(
            {
                "name": _bird_symbol(session.name),
                "neighbor": session.neighbor.split("%", 1)[0],
                "asn": session.remote_asn,
                "source_address": session.source_address,
            }
        )
    return collectors


def _session_neighbor(sessions: list[Any], family: AddressFamily) -> str | None:
    version = 4 if family == AddressFamily.IPV4 else 6
    for session in sorted(sessions, key=lambda item: item.name):
        neighbor = session.neighbor.split("%", 1)[0]
        if session.address_family == family:
            return neighbor
        if (
            session.address_family == AddressFamily.MP_BGP
            and ip_address(neighbor).version == version
        ):
            return neighbor
    return None


def _session_options(sessions: list[Any]) -> list[str]:
    options: list[str] = []
    if any(session.bfd and session.bfd.enabled for session in sessions):
        options.append("bfd yes;")
    return options


def bird_protocol_name(session_name: str) -> str:
    """把 BGP 会话名规范化为渲染出的 BIRD protocol 标识符。

    渲染与观测共用这一个函数：agent 解析 ``birdc show protocols`` 输出时，
    用它从会话名正推 protocol 名，保证两边永远一致。
    """

    sanitized = re.sub(r"[^A-Za-z0-9_]", "_", session_name)
    if not sanitized or sanitized[0].isdigit():
        sanitized = f"_{sanitized}"
    return sanitized


# 模块内部沿用简短别名。
_bird_symbol = bird_protocol_name


def _session_protocol_name(sessions: list[Any], family: AddressFamily) -> str | None:
    """返回指定地址族会话的 protocol 名（取 BgpSessionSpec.name）。"""

    for session in sorted(sessions, key=lambda item: item.name):
        if session.address_family == family:
            return _bird_symbol(session.name)
    return None


def _first_session_value(sessions: list[Any], field: str) -> Any:
    return getattr(sorted(sessions, key=lambda item: item.name)[0], field)


def _get_dn42_remote_ip(peer_config: dict[str, Any], af_type: int) -> str:
    return peer_config[f"peer_v{af_type}"]


def _jinja_log(value: int | float, base: int | float = math.e) -> float:
    return math.log(value, base)
