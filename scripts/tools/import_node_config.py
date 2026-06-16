from __future__ import annotations

"""把一个「已落地的节点配置目录」解析成 ``DesiredState`` 并导入控制面 DB。

适用于把存量手写配置（bird.conf + peers/*.conf + wireguard/*.conf +
scripts/wg/apply-*.sh）一次性迁移进控制面，使该节点成为受控节点。

解析来源与映射关系::

    bird/bird.conf                      ->  NodeSpec（OWNAS / OWNIP / OWNNET ...）+ RPKI
    scripts/wg/apply-*.sh               ->  每个接口的地址 / 对端路由 / dummy|wireguard 类型
    wireguard/<iface>.conf              ->  WireGuard 私钥 / 监听端口 / 对端公钥 / Endpoint
    bird/peers/*.conf                   ->  BgpSessionSpec（neighbor / asn / source / bfd ...）

接口与会话的关联：优先用 ``neighbor`` 里的 ``%zone`` 链路本地区，其次用
``neighbor`` 地址命中某个接口的 ``peer_routes`` 反查接口；都没有则视作
multihop / 无绑定接口（``interface=None``）。

用法::

    python scripts/tools/import_node_config.py 迁移配置/hkg1 \
        --node-id edge1 --site hkg --agent-token edge1-token

默认直接写入 ``DN42_CONTROL_DATABASE_URL``（未设置则用控制面默认 control.db）。
``--dry-run`` 只解析并打印 DesiredState JSON，不落库。
"""

import argparse
import asyncio
import ipaddress
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 源码即包：把 packages 与 control-server 注入 sys.path，免去安装。
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (
    _REPO_ROOT / "apps" / "control-server",
    _REPO_ROOT / "packages" / "dn42_common",
    _REPO_ROOT / "packages" / "dn42_schemas",
    _REPO_ROOT / "packages" / "dn42_templates",
    _REPO_ROOT / "packages" / "dn42_runtime",
):
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from dn42_common import Dn42OriginRegionCommunity  # noqa: E402
from dn42_schemas import (  # noqa: E402
    AddressFamily,
    BfdSpec,
    BgpLargeCommunitySpec,
    BgpSessionSpec,
    Bird2ConfigSpec,
    BuildSpec,
    DesiredState,
    HealthCheckSpec,
    InterfaceKind,
    InterfaceSpec,
    LookglassSpec,
    NodeSpec,
    RouterDockerfileSpec,
    RouterRuntimeSpec,
    RuntimeServiceSpec,
    ServiceRole,
    TemplateSetSpec,
    UnderlayNetworkSpec,
    VolumeMount,
    WireGuardPeerSpec,
    WireGuardPortRangeSpec,
)


_ROUTER_SYSCTLS = {
    "net.ipv4.conf.all.rp_filter": "0",
    "net.ipv4.conf.default.rp_filter": "0",
    "net.ipv4.ip_forward": "1",
    "net.ipv6.conf.all.forwarding": "1",
    "net.ipv6.conf.default.forwarding": "1",
}


# --- 解析得到的中间结构 ------------------------------------------------------


@dataclass
class _ParsedInterface:
    name: str
    kind: InterfaceKind
    addresses: list[str] = field(default_factory=list)
    peer_routes: list[str] = field(default_factory=list)
    listen_port: int | None = None
    mtu: int | None = None
    private_key: str | None = None
    public_key: str | None = None
    endpoint: str | None = None
    allowed_ips: list[str] = field(default_factory=list)
    preshared_key: str | None = None
    keepalive: int | None = None


# --- bird.conf：节点身份 ----------------------------------------------------


def _parse_node_identity(bird_conf: str) -> dict[str, str]:
    defines: dict[str, str] = {}
    for match in re.finditer(r"define\s+(\w+)\s*=\s*([^;]+);", bird_conf):
        defines[match.group(1)] = match.group(2).strip()
    return defines


# --- apply 脚本：地址 / 对端路由 / 接口类型 ---------------------------------


def _resolve_shell_vars(script: str) -> str:
    """把 apply 脚本里 ``VAR="value"`` 赋值就地展开成实际值。"""

    assigns: dict[str, str] = {}
    for line in script.splitlines():
        stripped = line.strip()
        m = re.match(r'^(\w+)=(?:"([^"]*)"|(\S+))\s*$', stripped)
        if m:
            assigns[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)

    def _sub(text: str) -> str:
        # 多轮替换以覆盖嵌套（值里引用其他变量的情况）。
        for _ in range(5):
            new = re.sub(
                r"\$\{(\w+)\}|\$(\w+)",
                lambda mo: assigns.get(mo.group(1) or mo.group(2), mo.group(0)),
                text,
            )
            if new == text:
                break
            text = new
        return text

    return _sub(script)


def _parse_apply_script(path: Path) -> _ParsedInterface | None:
    raw = path.read_text(encoding="utf-8", errors="replace")
    resolved = _resolve_shell_vars(raw)

    if_match = re.search(r'^\s*IF=(?:"([^"]+)"|(\S+))', resolved, re.MULTILINE)
    if not if_match:
        return None
    ifname = if_match.group(1) or if_match.group(2)

    kind = (
        InterfaceKind.DUMMY
        if re.search(r"type\s+dummy", resolved)
        else InterfaceKind.WIREGUARD
    )
    parsed = _ParsedInterface(name=ifname, kind=kind)

    for m in re.finditer(
        r"ip(?:\s+-6)?\s+addr\s+replace\s+\"?([^\"\s]+)\"?"
        r'(?:\s+peer\s+\"?([^\"\s]+)\"?)?\s+dev',
        resolved,
    ):
        address, peer = m.group(1), m.group(2)
        if _looks_like_ip_iface(address):
            parsed.addresses.append(address)
        if peer and _looks_like_ip_iface(peer):
            parsed.peer_routes.append(peer)

    for m in re.finditer(
        r"ip(?:\s+-6)?\s+route\s+replace\s+\"?([^\"\s]+)\"?\s+dev", resolved
    ):
        route = m.group(1)
        if _looks_like_ip_iface(route):
            parsed.peer_routes.append(route)

    mtu_match = re.search(r'ip\s+link\s+set\s+\"?\$?\{?\w*\}?\"?\s+mtu\s+(\d+)', resolved)
    if mtu_match:
        parsed.mtu = int(mtu_match.group(1))

    parsed.addresses = _dedupe(parsed.addresses)
    parsed.peer_routes = _dedupe(parsed.peer_routes)
    return parsed


# --- wireguard/<iface>.conf -------------------------------------------------


def _parse_wireguard_conf(path: Path) -> dict[str, object]:
    data: dict[str, object] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        if key == "privatekey":
            data["private_key"] = value
        elif key == "listenport":
            data["listen_port"] = int(value)
        elif key == "publickey":
            data["public_key"] = value
        elif key == "presharedkey":
            data["preshared_key"] = value
        elif key == "endpoint":
            data["endpoint"] = value
        elif key == "persistentkeepalive":
            data["keepalive"] = int(value)
        elif key == "allowedips":
            data["allowed_ips"] = [item.strip() for item in value.split(",") if item.strip()]
    return data


# --- bird/peers/*.conf：BGP 会话 -------------------------------------------


def _iter_bgp_blocks(text: str):
    """逐个 yield ``(name, block_body)``，按花括号配对切块。"""

    for header in re.finditer(r"protocol\s+bgp\s+(\S+)", text):
        name = header.group(1)
        brace = text.find("{", header.end())
        if brace == -1:
            continue
        depth = 0
        for idx in range(brace, len(text)):
            if text[idx] == "{":
                depth += 1
            elif text[idx] == "}":
                depth -= 1
                if depth == 0:
                    yield name, text[brace : idx + 1]
                    break


def _parse_bfd(block: str) -> BfdSpec | None:
    if not re.search(r"\bbfd\s+(on|graceful)", block):
        return None
    interval_ms = 1000
    multiplier = 5
    m = re.search(r"interval\s+(\d+)\s*(ms|s)?", block)
    if m:
        value = int(m.group(1))
        interval_ms = value * 1000 if (m.group(2) or "ms") == "s" else value
    mm = re.search(r"multiplier\s+(\d+)", block)
    if mm:
        multiplier = int(mm.group(1))
    return BfdSpec(interval_ms=max(interval_ms, 50), multiplier=multiplier)


def _address_family(name: str, block: str, neighbor: str) -> AddressFamily:
    low = name.lower()
    if low.endswith("_v6_v4") or low.endswith("_v4_v6"):
        return AddressFamily.MP_BGP
    if low.endswith("_v4"):
        return AddressFamily.IPV4
    if low.endswith("_v6"):
        return AddressFamily.IPV6
    has4 = re.search(r"\bipv4\s*\{", block) is not None
    has6 = re.search(r"\bipv6\s*\{", block) is not None
    if has4 and has6:
        return AddressFamily.MP_BGP
    if has6 and not has4:
        return AddressFamily.IPV6
    if has4 and not has6:
        return AddressFamily.IPV4
    return AddressFamily.IPV6 if ":" in neighbor else AddressFamily.IPV4


@dataclass
class _ParsedSession:
    name: str
    remote_asn: int
    neighbor: str
    source_address: str
    address_family: AddressFamily
    zone: str | None
    bfd: BfdSpec | None
    route_reflector_client: bool
    extended_next_hop: bool
    internal: bool


def _parse_peer_file(path: Path, own_asn: int) -> list[_ParsedSession]:
    text = path.read_text(encoding="utf-8", errors="replace")
    sessions: list[_ParsedSession] = []
    for name, block in _iter_bgp_blocks(text):
        nm = re.search(r"neighbor\s+(\S+?)\s+as\s+(\d+)", block)
        sm = re.search(r"source\s+address\s+([^\s;]+)", block)
        if not nm or not sm:
            continue
        neighbor = nm.group(1)
        remote_asn = int(nm.group(2))
        address, zone = _split_zone(neighbor)
        sessions.append(
            _ParsedSession(
                name=name,
                remote_asn=remote_asn,
                neighbor=neighbor,
                source_address=sm.group(1),
                address_family=_address_family(name, block, address),
                zone=zone,
                bfd=_parse_bfd(block),
                route_reflector_client=bool(re.search(r"\brr\s+client", block)),
                extended_next_hop=bool(re.search(r"extended\s+next\s+hop\s+on", block)),
                internal=remote_asn == own_asn,
            )
        )
    return sessions


# --- 工具 -------------------------------------------------------------------


def _split_zone(value: str) -> tuple[str, str | None]:
    if "%" in value:
        addr, _, zone = value.partition("%")
        return addr, zone
    return value, None


def _looks_like_ip_iface(value: str) -> bool:
    try:
        ipaddress.ip_interface(value)
        return True
    except ValueError:
        return False


def _bare_ip(value: str) -> str:
    return value.split("/", 1)[0]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# --- 组装 DesiredState ------------------------------------------------------


def _default_router_runtime(
    *, wg_port_start: int = 51800, wg_port_end: int = 51899
) -> RouterRuntimeSpec:
    return RouterRuntimeSpec(
        underlay=UnderlayNetworkSpec(subnet="10.254.42.0/24", gateway="10.254.42.1"),
        router_dockerfile=RouterDockerfileSpec(),
        wireguard_port_range=WireGuardPortRangeSpec(start=wg_port_start, end=wg_port_end),
        services=[
            RuntimeServiceSpec(
                name="dn42-router-netns",
                role=ServiceRole.ROUTER_NETNS,
                build=BuildSpec(target="netns"),
                command=["sleep", "infinity"],
                cap_add=["NET_ADMIN", "NET_RAW"],
                devices=["/dev/net/tun:/dev/net/tun"],
                sysctls=_ROUTER_SYSCTLS,
                healthcheck=HealthCheckSpec(
                    test=[
                        "CMD-SHELL",
                        "ip link show lo >/dev/null && ip link show eth0 >/dev/null",
                    ],
                ),
            ),
            RuntimeServiceSpec(
                name="dn42-wg-gateway",
                role=ServiceRole.WG_GATEWAY,
                build=BuildSpec(target="wg-gateway"),
                command=["/opt/dn42/scripts/wg/start-wg-gateway.sh"],
                network_mode="service:dn42-router-netns",
                cap_add=["NET_ADMIN", "NET_RAW"],
                devices=["/dev/net/tun:/dev/net/tun"],
                volumes=[
                    VolumeMount(source="wireguard", target="/etc/wireguard"),
                    VolumeMount(source="scripts", target="/opt/dn42/scripts"),
                ],
                depends_on=["dn42-router-netns"],
            ),
            RuntimeServiceSpec(
                name="dn42-bird-router",
                role=ServiceRole.BIRD_ROUTER,
                build=BuildSpec(target="bird-router"),
                command=["/opt/dn42/scripts/bird/start-bird-router.sh"],
                network_mode="service:dn42-router-netns",
                cap_add=["NET_ADMIN", "NET_RAW"],
                volumes=[
                    VolumeMount(source="bird", target="/etc/bird"),
                    VolumeMount(source="scripts", target="/opt/dn42/scripts"),
                ],
                depends_on=["dn42-router-netns", "dn42-wg-gateway", "dn42-rpki-cache"],
            ),
            RuntimeServiceSpec(
                name="dn42-rpki-cache",
                role=ServiceRole.RPKI_CACHE,
                image="rpki/stayrtr:latest",
                command=[
                    "-checktime=false",
                    "-cache=https://dn42.burble.com/roa/dn42_roa_46.json",
                ],
            ),
        ],
    )


def _build_interface(parsed: _ParsedInterface) -> InterfaceSpec:
    if parsed.kind == InterfaceKind.DUMMY:
        return InterfaceSpec(
            name=parsed.name,
            kind=InterfaceKind.DUMMY,
            mtu=None,
            addresses=parsed.addresses,
        )
    if not parsed.private_key:
        raise ValueError(f"wireguard 接口 {parsed.name} 缺少 PrivateKey")
    if not parsed.public_key:
        raise ValueError(f"wireguard 接口 {parsed.name} 缺少对端 PublicKey")
    peer = WireGuardPeerSpec(
        public_key=parsed.public_key,
        preshared_key_ref=parsed.preshared_key,
        endpoint=parsed.endpoint,
        allowed_ips=parsed.allowed_ips or ["0.0.0.0/0", "::/0"],
        persistent_keepalive_seconds=parsed.keepalive,
    )
    return InterfaceSpec(
        name=parsed.name,
        kind=InterfaceKind.WIREGUARD,
        addresses=parsed.addresses,
        peer_routes=parsed.peer_routes,
        listen_port=parsed.listen_port,
        mtu=parsed.mtu if parsed.mtu is not None else 1420,
        private_key_ref=parsed.private_key,
        wireguard_peer=peer,
    )


def _resolve_session_interface(
    session: _ParsedSession, interfaces: list[InterfaceSpec]
) -> str | None:
    if session.zone:
        return session.zone
    neighbor_ip = _bare_ip(session.neighbor)
    for interface in interfaces:
        if any(_bare_ip(route) == neighbor_ip for route in interface.peer_routes):
            return interface.name
    return None


def build_state_from_config_dir(
    config_dir: Path,
    *,
    node_id: str,
    site: str,
    region: Dn42OriginRegionCommunity = Dn42OriginRegionCommunity.ASIA_EAST,
    wg_port_start: int = 51800,
    wg_port_end: int = 51899,
) -> DesiredState:
    """解析节点配置目录，返回完整 ``DesiredState``。"""

    bird_conf_path = config_dir / "bird" / "bird.conf"
    defines = _parse_node_identity(bird_conf_path.read_text(encoding="utf-8"))
    own_asn = int(defines["OWNAS"])

    # 1) 接口：以 apply 脚本为权威来源（含地址 / 对端路由 / 类型）。
    parsed_ifaces: dict[str, _ParsedInterface] = {}
    apply_dir = config_dir / "scripts" / "wg"
    for script in sorted(apply_dir.glob("apply-*.sh")):
        if script.name == "apply-all-wg.sh":
            continue
        parsed = _parse_apply_script(script)
        if parsed is not None:
            parsed_ifaces[parsed.name] = parsed

    # 2) 给 wireguard 接口补上密钥 / 监听端口 / 对端信息。
    wg_dir = config_dir / "wireguard"
    for parsed in parsed_ifaces.values():
        if parsed.kind != InterfaceKind.WIREGUARD:
            continue
        conf = wg_dir / f"{parsed.name}.conf"
        if not conf.exists():
            raise ValueError(f"接口 {parsed.name} 缺少 wireguard 配置 {conf}")
        wg = _parse_wireguard_conf(conf)
        parsed.private_key = wg.get("private_key")  # type: ignore[assignment]
        parsed.public_key = wg.get("public_key")  # type: ignore[assignment]
        parsed.endpoint = wg.get("endpoint")  # type: ignore[assignment]
        parsed.preshared_key = wg.get("preshared_key")  # type: ignore[assignment]
        parsed.listen_port = wg.get("listen_port")  # type: ignore[assignment]
        parsed.keepalive = wg.get("keepalive")  # type: ignore[assignment]
        parsed.allowed_ips = list(wg.get("allowed_ips", []))  # type: ignore[arg-type]

    # dummy loopback 优先排前，其余按名稳定排序。
    ordered = sorted(
        parsed_ifaces.values(),
        key=lambda p: (p.kind != InterfaceKind.DUMMY, p.name),
    )
    interfaces = [_build_interface(p) for p in ordered]

    # 3) BGP 会话。
    sessions: list[BgpSessionSpec] = []
    peers_dir = config_dir / "bird" / "peers"
    parsed_sessions: list[_ParsedSession] = []
    for peer_file in sorted(peers_dir.glob("*.conf")):
        parsed_sessions.extend(_parse_peer_file(peer_file, own_asn))

    for ps in parsed_sessions:
        interface = _resolve_session_interface(ps, interfaces)
        sessions.append(
            BgpSessionSpec(
                name=ps.name,
                remote_asn=ps.remote_asn,
                neighbor=ps.neighbor,
                source_address=ps.source_address,
                address_family=ps.address_family,
                interface=interface,
                policy="internal" if ps.internal else "dnpeers",
                extended_next_hop=ps.extended_next_hop,
                bfd=ps.bfd,
                route_reflector_client=ps.route_reflector_client,
            )
        )

    # 4) 节点身份 + bird 高层配置。
    loopback_ipv4 = defines.get("OWNIP")
    origin_node_id = None
    if loopback_ipv4:
        try:
            origin_node_id = int(loopback_ipv4.split(".")[-1])
        except ValueError:
            origin_node_id = None

    node = NodeSpec(
        node_id=node_id,
        site=site,
        region=region,
        asn=own_asn,
        router_id=defines["OWNIP"],
        ipv4_prefixes=[defines["OWNNET"]] if "OWNNET" in defines else [],
        ipv6_prefixes=[defines["OWNNETv6"]] if "OWNNETv6" in defines else [],
        loopback_ipv4=loopback_ipv4,
        loopback_ipv6=defines.get("OWNIPv6"),
    )

    bird = Bird2ConfigSpec(
        region=region,
        large_communities=BgpLargeCommunitySpec(origin_node_id=origin_node_id),
    )

    return DesiredState(
        generation=1,
        node=node,
        runtime=_default_router_runtime(
            wg_port_start=wg_port_start, wg_port_end=wg_port_end
        ),
        bird=bird,
        interfaces=interfaces,
        bgp_sessions=sessions,
        dns=None,
        lookglass=LookglassSpec(
            frontend_enabled=True,
            allowed_ips=["10.254.42.0/24"],
            published_frontend_ports=["5000:5000"],
            title_brand="DN42 looking glass",
            navbar_brand="DN42",
        ),
        templates=TemplateSetSpec(coredns=None),
    )


# --- 落库 -------------------------------------------------------------------


async def _import_to_database(database_url: str, state: DesiredState, agent_token: str | None) -> None:
    from app.db import Base, Database, provision_node_from_state

    database = Database(database_url)
    try:
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with database.session() as session:
            desired = await provision_node_from_state(
                session,
                state,
                agent_token=agent_token,
                reason=f"import {state.node.node_id} from on-disk config",
            )
        print(
            f"导入完成：node_id={desired.node.node_id} generation={desired.generation} "
            f"interfaces={len(desired.interfaces)} bgp_sessions={len(desired.bgp_sessions)}"
        )
    finally:
        await database.dispose()


def _import_via_controller(
    controller_url: str,
    state: DesiredState,
    agent_token: str | None,
    *,
    admin_token: str | None = None,
    timeout: float = 30.0,
) -> None:
    """通过控制面 HTTP ``POST /api/v1/admin/provision`` 导入（替代直连 DB）。

    生产环境控制面与 importer 可能不在同一台机器，直连 DB 既不安全也不可行；
    这条路径让 importer 只跟控制面 API 打交道，由控制面负责落库 + 广播 doorbell。
    """

    import httpx

    base = controller_url.rstrip("/")
    url = f"{base}/api/v1/admin/provision"
    payload: dict[str, object] = {"state": state.model_dump(mode="json")}
    if agent_token is not None:
        payload["agent_token"] = agent_token
    headers = {}
    if admin_token:
        headers["Authorization"] = f"Bearer {admin_token}"

    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise SystemExit(
            f"控制面 provision 失败：HTTP {response.status_code} {response.text}"
        )
    body = response.json()
    print(
        f"导入完成（经控制面）：node_id={body.get('node_id')} "
        f"generation={body.get('generation')} "
        f"subscribers={body.get('subscribers')} delivered={body.get('delivered')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把节点配置目录导入控制面 DB")
    parser.add_argument("config_dir", type=Path, help="节点配置目录，例如 迁移配置/hkg1")
    parser.add_argument("--node-id", required=True, help="目标节点 node_id")
    parser.add_argument("--site", default=None, help="站点标识（默认取 node-id 第一段）")
    parser.add_argument(
        "--region",
        default=Dn42OriginRegionCommunity.ASIA_EAST.name,
        help="DN42 区域枚举名（默认 ASIA_EAST）",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DN42_CONTROL_DATABASE_URL"),
        help="目标 SQLAlchemy 异步 DSN（默认取 DN42_CONTROL_DATABASE_URL / 控制面默认库）",
    )
    parser.add_argument(
        "--controller-url",
        default=os.environ.get("DN42_CONTROL_URL"),
        help=(
            "控制面基础 URL（如 http://127.0.0.1:8000）。设置后改走 "
            "POST /api/v1/admin/provision，不再直连 DB（推荐用于远程控制面）。"
        ),
    )
    parser.add_argument(
        "--admin-token",
        default=os.environ.get("DN42_CONTROL_ADMIN_TOKEN"),
        help="可选：调用控制面 admin API 时使用的 Bearer token",
    )
    parser.add_argument("--agent-token", default=None, help="为该节点绑定的 agent Bearer token")
    parser.add_argument(
        "--wg-port-range",
        default="51800-51899",
        help="router-netns 发布的 WireGuard 监听 UDP 端口范围，格式 start-end（默认 51800-51899）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只解析并打印 DesiredState JSON，不落库")
    args = parser.parse_args(argv)

    config_dir: Path = args.config_dir
    if not config_dir.is_dir():
        parser.error(f"配置目录不存在：{config_dir}")

    try:
        wg_start_str, wg_end_str = args.wg_port_range.split("-", 1)
        wg_port_start = int(wg_start_str)
        wg_port_end = int(wg_end_str)
    except ValueError:
        parser.error(f"--wg-port-range 格式应为 start-end，收到：{args.wg_port_range!r}")
    if not (1 <= wg_port_start <= wg_port_end <= 65535):
        parser.error(f"--wg-port-range 非法：{args.wg_port_range!r}")

    site = args.site or args.node_id.split("-", 1)[0]
    region = Dn42OriginRegionCommunity[args.region]

    state = build_state_from_config_dir(
        config_dir,
        node_id=args.node_id,
        site=site,
        region=region,
        wg_port_start=wg_port_start,
        wg_port_end=wg_port_end,
    )

    if args.dry_run:
        print(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    if args.controller_url:
        _import_via_controller(
            args.controller_url,
            state,
            args.agent_token,
            admin_token=args.admin_token,
        )
        return 0

    database_url = args.database_url
    if not database_url:
        from app.core.config import ControlServerConfig

        database_url = ControlServerConfig().database_url

    asyncio.run(_import_to_database(database_url, state, args.agent_token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
