from __future__ import annotations

"""两台内部节点加一台 eBGP 外部节点的完整 API 示例。

这个脚本不是为了展示“最少几行代码能渲染出一个节点”，而是为了展示：

1. 如何只通过基础包公开 API 构造完整 `DesiredState`
2. 如何把拓扑常量翻译成可启动的多目录实验产物
3. 如何在单宿主机场景下处理多 compose deployment 之间的 WireGuard 互通

脚本内直接描述了一套最小但足够真实的 DN42 实验：

- `edge1` 与 `pvg1-edge` 属于 AS4242420000，其中 `pvg1-edge` 用来模拟中国上海节点
- 二者之间有一条 IGP WireGuard 链路，用来承载 OSPF 和 iBGP
- `hutao-peer` 属于 AS4242420002（HUTAO），并分别与两台内部节点建立 eBGP WireGuard 邻接

和旧的单节点示例相比，这个版本更接近真实部署，因为它同时覆盖了：

- internal_topology 与显式 eBGP session 的分工
- 每节点独立 underlay / compose project 的渲染方式
- 宿主机 UDP 端口映射如何支撑跨 deployment WireGuard 建邻
- 不依赖 `secret://` 解析即可直接启动的本地实验
"""

from dataclasses import dataclass
from pathlib import Path
import shutil
from textwrap import dedent

from dn42_common import Dn42OriginRegionCommunity
from dn42_runtime import write_rendered_files
from dn42_schemas import (
    AddressFamily,
    BgpSessionSpec,
    Bird2ConfigSpec,
    BirdHostSpec,
    BuildSpec,
    DesiredState,
    HealthCheckSpec,
    IgpAdjacencySpec,
    InterfaceKind,
    InterfaceSpec,
    InternalTopologySpec,
    NodeSpec,
    PortPublishSpec,
    RouterDockerfileSpec,
    RouterRuntimeSpec,
    RpkiSpec,
    RuntimeServiceSpec,
    ServiceRole,
    TemplateSetSpec,
    UnderlayNetworkSpec,
    VolumeMount,
    WireGuardPeerSpec,
)
from dn42_templates import render_desired_state


OUTPUT_DIR = Path("tmp/two-internal-one-ebgp-demo")
DEFAULT_ROUTER_BASE_IMAGE = "debian:13-slim"
DEFAULT_DEBIAN_MIRROR = "deb.debian.org"
HOST_ENDPOINT = "host.docker.internal"
ASN = 4242420000
EBGP_ASN = 4242420002
PREFIX4 = "172.20.0.0/26"
PREFIX6 = "fdce:1111:2222::/48"
EBGP_PREFIX4 = "172.20.1.0/26"
EBGP_PREFIX6 = "fdce:3333:4444::/48"

ROUTER_SYSCTLS = {
    "net.ipv4.conf.all.rp_filter": "0",
    "net.ipv4.conf.default.rp_filter": "0",
    "net.ipv4.ip_forward": "1",
    "net.ipv6.conf.all.forwarding": "1",
    "net.ipv6.conf.default.forwarding": "1",
}

# 下面这些常量就是整个示例的“拓扑数据库”：
# - NODES 记录内部 AS4242420000 的节点部署参数
# - EBGP_NODE 记录外部 AS4242420002 的节点部署参数
# - IGP_LINK 描述内部链路
# - EBGP_LINKS 描述每个内部节点到外部节点的边界链路
#
# 后面的 helper 不会重新发明第二套输入来源，而是只负责把这些常量稳定地翻译成
# schema 对象与最终文件集。这也是这个示例最想展示的设计点之一。


@dataclass(frozen=True, slots=True)
class Node:
    """示例脚本内部使用的节点输入模型。

    它不是公共 schema，而是示例专用的一层“拓扑输入”。
    把节点级差异集中在这里，是为了让后面的构造函数只关注“如何翻译成 DesiredState”，
    而不是同时背负一堆零散常量。

    字段大体可分成三类：

    - 身份与输出目录：`node_id`、`directory`、`prefix`
    - 本地部署差异：underlay 子网、RPKI 监听 IP、Dockerfile 覆盖项
    - 跨 deployment 互通：WireGuard 密钥、映射到宿主机的 UDP 端口
    """

    node_id: str
    directory: str
    prefix: str
    underlay_subnet: str
    underlay_gateway: str
    rpki_ip: str
    router_id: str
    loopback_ipv6: str
    link_local_ipv6: str
    private_key: str
    public_key: str
    igp_host_ports: dict[str, int]
    ebgp_host_ports: dict[str, int]
    router_base_image: str = DEFAULT_ROUTER_BASE_IMAGE
    debian_mirror: str = DEFAULT_DEBIAN_MIRROR


@dataclass(frozen=True, slots=True)
class Link:
    """内部 IGP 链路的双端地址与成本定义。"""

    left: str
    right: str
    port: int
    left_v4: str
    right_v4: str
    left_v6: str
    right_v6: str
    cost: int


@dataclass(frozen=True, slots=True)
class EbgpLink:
    """内部节点到外部 AS 节点的一条 eBGP 链路定义。"""

    internal_node_id: str
    port: int
    internal_v4: str
    external_v4: str
    internal_v6: str
    external_v6: str


NODES = {
    "edge1": Node(
        "edge1",
        "hkg1",
        "hkg1",
        "10.254.51.0/24",
        "10.254.51.1",
        "10.254.51.10",
        "172.20.0.62",
        "fdce:1111:2222:9500::1",
        "fe80::202:62",
        "Z880QqxvK4PEyBSglz+lBqfieuUtm1j+/Jh9JiRTenk=",
        "ZlwS4fpFyiMeaIvV//D8nTYJlX61uRaoOdmPjSbM6QY=",
        igp_host_ports={"pvg1-edge": 32001},
        ebgp_host_ports={"hutao-peer": 32129},
    ),
    "pvg1-edge": Node(
        "pvg1-edge",
        "pvg1",
        "pvg1",
        "10.254.52.0/24",
        "10.254.52.1",
        "10.254.52.10",
        "172.20.0.63",
        "fdce:1111:2222:9501::1",
        "fe80::202:63",
        "PWOvDKXDilTsKTG/hChb3gtQBZVyx93hNgN6DROceFw=",
        "+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=",
        igp_host_ports={"edge1": 32002},
        ebgp_host_ports={"hutao-peer": 32130},
    ),
}

# hutao-peer 是专门用来承载外部 AS4242420002（HUTAO）邻居的虚拟节点。
# 它不会进入 AS4242420000 的 internal_topology，只会通过显式 eBGP session 与内部节点交互。
EBGP_NODE = Node(
    "hutao-peer",
    "hutao",
    "hutao",
    "10.254.53.0/24",
    "10.254.53.1",
    "10.254.53.10",
    "172.20.1.1",
    "fdce:3333:4444::1",
    "fe80::715:29",
    "BnwFvdpglpMZ/jtPF+JNxhRNn/VGsnIg4X9b+pQA+i0=",
    "YAXcjFd6X26WrFRfcKv2g8RV0r24rKNijQ9gBKaWFmc=",
    igp_host_ports={},
    ebgp_host_ports={"edge1": 32229, "pvg1-edge": 32230},
)

# 这条链路只属于内部 AS，用来承载 OSPF 与 iBGP 的底层连通性。
IGP_LINK = Link(
    "edge1",
    "pvg1-edge",
    30001,
    "198.18.10.0",
    "198.18.10.1",
    "fdce:1111:2222:ff10::0",
    "fdce:1111:2222:ff10::1",
    10,
)

# 内部每个节点都各自有一条到 hutao-peer 的 eBGP WireGuard 链路。
# 这样实验中可以同时验证：
# - 内部 OSPF / iBGP 是否正常建立
# - 外部 AS 对多条边界链路的 eBGP 是否正常建立
EBGP_LINKS = {
    "edge1": EbgpLink(
        "edge1",
        31029,
        "198.18.20.0",
        "198.18.20.1",
        "fdce:1111:2222:ef29::0",
        "fdce:1111:2222:ef29::1",
    ),
    "pvg1-edge": EbgpLink(
        "pvg1-edge",
        31030,
        "198.18.20.2",
        "198.18.20.3",
        "fdce:1111:2222:ef2a::0",
        "fdce:1111:2222:ef2a::1",
    ),
}


def published_wireguard_ports(node: Node) -> list[PortPublishSpec]:
    """把拓扑层的端口分配翻译成 runtime schema 的端口发布列表。

    因为这里是“三个独立 compose deployment 同机运行”的实验，节点之间不能直接依赖
    同一个 bridge 网络里的服务名通信。跨 deployment 的 WireGuard 建邻只能通过：

    - 每个节点在本地 `router-netns` service 上监听固定容器内 UDP 端口
    - 再把这些端口映射到宿主机的唯一 host_port
    - 对端通过 `host.docker.internal:<host_port>` 回拨
    """

    ports: list[PortPublishSpec] = []
    if node.node_id in NODES:
        ports.append(
            PortPublishSpec(
                host_port=node.igp_host_ports[peer_id(IGP_LINK, node.node_id)],
                container_port=IGP_LINK.port,
                protocol="udp",
            )
        )
    for link in ebgp_links_for_node(node.node_id):
        peer_node_id = EBGP_NODE.node_id if node.node_id != EBGP_NODE.node_id else link.internal_node_id
        ports.append(
            PortPublishSpec(
                host_port=node.ebgp_host_ports[peer_node_id],
                container_port=link.port,
                protocol="udp",
            )
        )
    return sorted(ports, key=lambda item: (item.host_port or 0, item.container_port, item.protocol))


def runtime_services(node: Node) -> list[RuntimeServiceSpec]:
    """构造一个节点的 runtime 服务集合。

    这里故意不直接写 compose YAML，而是继续走 runtime schema，目的是让这个示例完整覆盖：

    - `dn42_schemas` 如何表达 runtime service
    - `dn42_runtime` 如何把它们渲染成 compose 文件
    - 节点之间的差异如何通过输入参数而不是字符串模板分叉来表达
    """

    return [
        RuntimeServiceSpec(
            name="dn42-router-netns",
            role=ServiceRole.ROUTER_NETNS,
            build=BuildSpec(target="netns"),
            command=["sleep", "infinity"],
            cap_add=["NET_ADMIN", "NET_RAW"],
            devices=["/dev/net/tun:/dev/net/tun"],
            ports=published_wireguard_ports(node),
            sysctls=ROUTER_SYSCTLS,
            healthcheck=HealthCheckSpec(
                test=["CMD-SHELL", "ip link show lo >/dev/null && ip link show eth0 >/dev/null"],
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
                VolumeMount(source="scripts", target="/opt/dn42/scripts"),
                VolumeMount(source="wireguard", target="/etc/wireguard"),
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
                VolumeMount(source="scripts", target="/opt/dn42/scripts"),
                VolumeMount(source="bird", target="/etc/bird"),
                VolumeMount(source="runtime/bird-run", target="/run/bird", readonly=False),
            ],
            depends_on=["dn42-router-netns", "dn42-wg-gateway"],
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
    ]


def router_dockerfile_for_node(node: Node) -> RouterDockerfileSpec:
    """把节点级 Dockerfile 差异翻译成 runtime 的 Dockerfile 规范。"""

    return RouterDockerfileSpec(
        base_image=node.router_base_image,
        debian_mirror=node.debian_mirror,
    )


def peer_id(link: Link, node_id: str) -> str:
    """给定链路和本端节点，返回对端节点名。"""

    return link.right if link.left == node_id else link.left


def link_ips(link: Link, node_id: str) -> tuple[str, str, str, str]:
    """把链路左右端定义转换成“本端 / 对端视角”的地址元组。"""

    if link.left == node_id:
        return link.left_v4, link.right_v4, link.left_v6, link.right_v6
    return link.right_v4, link.left_v4, link.right_v6, link.left_v6


def internal_topology(node_id: str) -> InternalTopologySpec:
    """生成内部 AS4242420000 的 topology 视图。

    这里只包含两台内部节点，不包含 `hutao-peer`。这体现了一个重要边界：

    - AS 内部关系由 `internal_topology` 表达，用来驱动 OSPF 与 iBGP
    - 跨 AS 邻居由显式 `bgp_sessions` 表达
    """

    return InternalTopologySpec(
        routers=list(NODES),
        hosts={
            name: BirdHostSpec(ownip=node.router_id, ownip6=node.loopback_ipv6)
            for name, node in NODES.items()
        },
        igp_adjacencies=[IgpAdjacencySpec(node=peer_id(IGP_LINK, node_id), cost=IGP_LINK.cost)],
    )


def wg_interface(node: Node) -> InterfaceSpec:
    """构造内部 IGP WireGuard 接口。

    这条接口不只是“一个 VPN 隧道”，而是内部控制平面的承载层：

    - OSPFv2 / OSPFv3 通过它建邻
    - iBGP 通过它学到对端 loopback 的可达性
    - link-local IPv6 也会在这里显式出现，帮助 OSPFv3 保持稳定邻接身份
    """

    peer = NODES[peer_id(IGP_LINK, node.node_id)]
    local_v4, peer_v4, local_v6, peer_v6 = link_ips(IGP_LINK, node.node_id)
    return InterfaceSpec(
        name=f"igp-{peer.node_id}",
        kind=InterfaceKind.WIREGUARD,
        mtu=1420,
        listen_port=IGP_LINK.port,
        private_key_ref=node.private_key,
        addresses=[f"{local_v4}/31", f"{local_v6}/127", f"{node.link_local_ipv6}/64"],
        peer_routes=[f"{peer_v4}/32", f"{peer_v6}/128", f"{peer.link_local_ipv6}/128"],
        wireguard_peer=WireGuardPeerSpec(
            public_key=peer.public_key,
            endpoint=f"{HOST_ENDPOINT}:{peer.igp_host_ports[node.node_id]}",
            allowed_ips=["0.0.0.0/0", "::/0"],
            persistent_keepalive_seconds=5,
        ),
    )


def ebgp_links_for_node(node_id: str) -> list[EbgpLink]:
    """返回某个节点应该看到的 eBGP 链路集合。"""

    if node_id == EBGP_NODE.node_id:
        return list(EBGP_LINKS.values())
    link = EBGP_LINKS.get(node_id)
    return [link] if link else []


def ebgp_interfaces(node: Node) -> list[InterfaceSpec]:
    """构造 eBGP WireGuard 接口。

    这个 helper 同时处理两种视角：

    - 内部节点看外部节点
    - 外部节点看内部节点

    之所以集中在一个函数里，是因为最容易出错的几件事需要一起维护：

    - 本端 / 对端地址方向
    - endpoint 应该拨向谁发布出来的 host_port
    - 接口命名是 `as4242420002` 还是 `as0028-hkg1` / `as0028-pvg1` 这种外部视角名
    """

    interfaces: list[InterfaceSpec] = []
    for link in ebgp_links_for_node(node.node_id):
        internal_node = NODES[link.internal_node_id]
        is_internal_side = node.node_id != EBGP_NODE.node_id
        peer = EBGP_NODE if is_internal_side else internal_node
        local_v4 = link.internal_v4 if is_internal_side else link.external_v4
        peer_v4 = link.external_v4 if is_internal_side else link.internal_v4
        local_v6 = link.internal_v6 if is_internal_side else link.external_v6
        peer_v6 = link.external_v6 if is_internal_side else link.internal_v6
        interface_name = "as4242420002" if is_internal_side else f"as0028-{internal_node.prefix}"
        endpoint_port = peer.ebgp_host_ports[node.node_id]
        interfaces.append(
            InterfaceSpec(
                name=interface_name,
                kind=InterfaceKind.WIREGUARD,
                mtu=1420,
                listen_port=link.port,
                private_key_ref=node.private_key,
                addresses=[f"{local_v4}/31", f"{local_v6}/127"],
                peer_routes=[f"{peer_v4}/32", f"{peer_v6}/128"],
                wireguard_peer=WireGuardPeerSpec(
                    public_key=peer.public_key,
                    endpoint=f"{HOST_ENDPOINT}:{endpoint_port}",
                    allowed_ips=["0.0.0.0/0", "::/0"],
                    persistent_keepalive_seconds=5,
                ),
            )
        )
    return interfaces


def ebgp_sessions(node: Node) -> list[BgpSessionSpec]:
    """构造与 eBGP WireGuard 接口对应的 BGP session 列表。

    这里的命名规则刻意和模板层最终生成的 protocol suffix 保持一致，便于用 `birdc`
    直接把输出和这份示例代码对上：

    - 内部节点看到的是 `_hutao`
    - 外部节点看到的是 `_hkg1` / `_pvg1`
    """

    sessions: list[BgpSessionSpec] = []
    is_internal_side = node.node_id != EBGP_NODE.node_id
    for link in ebgp_links_for_node(node.node_id):
        internal_node = NODES[link.internal_node_id]
        interface = "as4242420002" if is_internal_side else f"as0028-{internal_node.prefix}"
        remote_asn = EBGP_ASN if is_internal_side else ASN
        peer_v4 = link.external_v4 if is_internal_side else link.internal_v4
        peer_v6 = link.external_v6 if is_internal_side else link.internal_v6
        local_v4 = link.internal_v4 if is_internal_side else link.external_v4
        local_v6 = link.internal_v6 if is_internal_side else link.external_v6
        suffix = "_hutao" if is_internal_side else f"_{internal_node.prefix}"
        name_suffix = "" if is_internal_side else f"_{internal_node.prefix}"
        sessions.extend(
            [
                BgpSessionSpec(
                    name=f"ebgp_{remote_asn}{name_suffix}_v4",
                    remote_asn=remote_asn,
                    neighbor=peer_v4,
                    source_address=local_v4,
                    address_family=AddressFamily.IPV4,
                    interface=interface,
                    protocol_suffix=suffix,
                    bfd=None,
                ),
                BgpSessionSpec(
                    name=f"ebgp_{remote_asn}{name_suffix}_v6",
                    remote_asn=remote_asn,
                    neighbor=peer_v6,
                    source_address=local_v6,
                    address_family=AddressFamily.IPV6,
                    interface=interface,
                    protocol_suffix=suffix,
                    bfd=None,
                ),
            ]
        )
    return sessions


def build_internal_state(node: Node) -> DesiredState:
    """把内部节点拓扑定义翻译成完整 DesiredState。

    内部节点会同时拥有：

    - dummy loopback
    - 一条 IGP WireGuard 接口
    - 一条通向外部 AS 的 eBGP WireGuard 接口
    - internal_topology，用来驱动模板层生成 OSPF 与 iBGP
    """

    interfaces = [
        InterfaceSpec(
            name="dn42-lo",
            kind=InterfaceKind.DUMMY,
            mtu=None,
            addresses=[f"{node.router_id}/32", f"{node.loopback_ipv6}/128"],
        ),
        wg_interface(node),
        *ebgp_interfaces(node),
    ]
    return DesiredState(
        generation=1,
        node=NodeSpec(
            node_id=node.node_id,
            site=node.prefix,
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            asn=ASN,
            router_id=node.router_id,
            ipv4_prefixes=[PREFIX4],
            ipv6_prefixes=[PREFIX6],
            loopback_ipv4=node.router_id,
            loopback_ipv6=node.loopback_ipv6,
        ),
        runtime=RouterRuntimeSpec(
            # 每个节点都拥有独立 underlay，因此三套 deployment 可以在同一宿主机上并行运行。
            underlay=UnderlayNetworkSpec(subnet=node.underlay_subnet, gateway=node.underlay_gateway),
            router_dockerfile=router_dockerfile_for_node(node),
            rpki=RpkiSpec(listen_host=node.rpki_ip),
            services=runtime_services(node),
        ),
        bird=Bird2ConfigSpec(
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            internal_topology=internal_topology(node.node_id),
            static_routes4=[f'{node.router_id}/32 via "dn42-lo"'],
            static_routes6=[f'{node.loopback_ipv6}/128 via "dn42-lo"'],
        ),
        interfaces=interfaces,
        bgp_sessions=ebgp_sessions(node),
        dns=None,
        templates=TemplateSetSpec(),
    )


def build_external_state() -> DesiredState:
    """把外部 AS 节点拓扑定义翻译成完整 DesiredState。"""

    node = EBGP_NODE
    # 外部节点和内部节点的建模差异，核心不在 runtime service，
    # 而在控制平面表达方式：
    # - 外部节点没有 internal_topology
    # - 它只依赖显式 eBGP session 与内部节点交换路由
    #
    # 也就是说，这个函数展示的是“当一个节点不属于内部 IGP / iBGP 域时，
    # 应该如何在同一套 schema 中被表达出来”。
    return DesiredState(
        generation=1,
        node=NodeSpec(
            node_id=node.node_id,
            site="lab",
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            asn=EBGP_ASN,
            router_id=node.router_id,
            ipv4_prefixes=[EBGP_PREFIX4],
            ipv6_prefixes=[EBGP_PREFIX6],
            loopback_ipv4=node.router_id,
            loopback_ipv6=node.loopback_ipv6,
        ),
        runtime=RouterRuntimeSpec(
            underlay=UnderlayNetworkSpec(subnet=node.underlay_subnet, gateway=node.underlay_gateway),
            router_dockerfile=router_dockerfile_for_node(node),
            rpki=RpkiSpec(listen_host=node.rpki_ip),
            services=runtime_services(node),
        ),
        bird=Bird2ConfigSpec(
            # 外部节点不参加内部 full-mesh iBGP/OSPF，因此这里没有 internal_topology。
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            static_routes4=[f'{node.router_id}/32 via "dn42-lo"'],
            static_routes6=[f'{node.loopback_ipv6}/128 via "dn42-lo"'],
        ),
        interfaces=[
            InterfaceSpec(
                name="dn42-lo",
                kind=InterfaceKind.DUMMY,
                mtu=None,
                addresses=[f"{node.router_id}/32", f"{node.loopback_ipv6}/128"],
            ),
            *ebgp_interfaces(node),
        ],
        bgp_sessions=ebgp_sessions(node),
        dns=None,
        templates=TemplateSetSpec(),
    )


def write_node(node: Node) -> None:
    """渲染并写出一个内部节点的配置文件目录。"""

    # `render_desired_state()` 会一次性生成：
    # - bird/*.conf
    # - wireguard/*.conf
    # - scripts/*
    #
    # 容器编排与镜像构建都不渲染文件：实际部署由 node-agent 按
    # `state.runtime` 的结构化数据经 Docker Engine API 完成（Dockerfile
    # 在 agent 内存生成）。这里的输出目录用于人工检查渲染产物。
    write_rendered_files(render_desired_state(build_internal_state(node)), OUTPUT_DIR / node.directory)


def write_external_node() -> None:
    """渲染并写出外部节点的完整 deployment 目录。"""

    # 外部节点单独走自己的 state builder，避免把内部 AS 的 topology 假设错误地复用到外部节点上。
    write_rendered_files(render_desired_state(build_external_state()), OUTPUT_DIR / EBGP_NODE.directory)


def write_readme() -> None:
    """生成与当前拓扑完全同步的 README。"""

    # README 也交给脚本生成，而不是手写静态文档，
    # 这样“代码里的拓扑”和“文档里的启动命令”永远不会漂移。
    (OUTPUT_DIR / "README.md").write_text(
        dedent(
            """
            # Two internal plus one eBGP DN42 lab

            This example renders three directories: `hkg1`, `pvg1`, and `hutao`.

            - `hkg1` and `pvg1` are AS4242420000 internal routers linked by one WireGuard IGP session plus iBGP.
            - `pvg1` represents a Shanghai node in this lab topology.
            - `hutao` is a virtual AS4242420002 edge router that forms one eBGP session with each internal node.
            - every node has its own Docker underlay subnet, local router Dockerfile, and local RPKI cache address.
            - the example is fully self-contained: the Python file embeds the topology, ports, and valid WireGuard key pairs.

            The rendered directories contain configuration files and image build
            contexts only. Container orchestration is data-driven: deploy these
            nodes by provisioning them into the control server database and
            letting node agents reconcile via the Docker Engine API.

            ```bash
            python scripts/dev/render-two-internal-one-ebgp-demo.py
            ```
            """
        ).lstrip(),
        encoding="utf-8",
    )


def main() -> None:
    """全量重渲染这个示例的输出目录。"""

    # 这里选择“先删再重写”，是为了保证示例目录永远是当前 Python 拓扑定义的精确投影，
    # 而不是一个混合了旧产物与新产物的半更新目录。
    #
    # 运行顺序也刻意保持简单：
    # 1. 渲染内部节点 hkg1
    # 2. 渲染内部节点 pvg1
    # 3. 渲染外部节点 hutao
    # 4. 生成说明文档
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    for node in NODES.values():
        write_node(node)
    write_external_node()
    write_readme()


if __name__ == "__main__":
    main()