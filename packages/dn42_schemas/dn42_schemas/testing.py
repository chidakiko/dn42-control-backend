from __future__ import annotations

"""黄金样本 fixture——HKG1 路由器的完整 `DesiredState`。

`build_hkg1_example_state()` 负责返回一个覆盖主要特性（内部拓扑 / eBGP /
DNS / lookglass / WireGuard 接口）的示例；`examples/rendered-hkg1/` 均由
本函数输出经完整管线渲染而成，是
`tests/unit/test_golden_rendered_hkg1.py` 的唯一输入源。
增删改均会造成黄金样本变动，请同步刷新快照（见 docs/README.md）。
"""

from dataclasses import dataclass, field

from dn42_common import Dn42OriginRegionCommunity
from dn42_schemas import (
    AddressFamily,
    BfdSpec,
    BgpLargeCommunitySpec,
    BgpSessionSpec,
    Bird2ConfigSpec,
    BirdHostSpec,
    BuildSpec,
    DesiredState,
    DnsForwardSpec,
    DnsSpec,
    DnsZoneSpec,
    HealthCheckSpec,
    IgpAdjacencySpec,
    InterfaceKind,
    InterfaceSpec,
    InternalTopologySpec,
    LookglassSpec,
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


ROUTER_SYSCTLS = {
    "net.ipv4.conf.all.rp_filter": "0",
    "net.ipv4.conf.default.rp_filter": "0",
    "net.ipv4.ip_forward": "1",
    "net.ipv6.conf.all.forwarding": "1",
    "net.ipv6.conf.default.forwarding": "1",
}


def build_hkg1_example_state() -> DesiredState:
    """返回 HKG1 示例路由器的完整 `DesiredState`。

    该函数是 `examples/rendered-hkg1/` 黄金样本的唯一源头，也被多个单测
    复用。修改时记得同步刷新 `examples/rendered-hkg1/` 下的黄金样本
    （见 packages/docs/README.md）。
    """

    return DesiredState(
        generation=1,
        node=NodeSpec(
            node_id="edge1",
            site="hkg",
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            asn=4242420000,
            router_id="172.20.0.62",
            ipv4_prefixes=["172.20.0.0/26"],
            ipv6_prefixes=["fdce:1111:2222::/48"],
            loopback_ipv4="172.20.0.62",
            loopback_ipv6="fdce:1111:2222:9500::1",
        ),
        runtime=RouterRuntimeSpec(
            underlay=UnderlayNetworkSpec(
                subnet="10.254.42.0/24",
                gateway="10.254.42.1",
            ),
            router_dockerfile=RouterDockerfileSpec(
                base_image="debian:13-slim",
                debian_mirror="deb.debian.org",
            ),
            services=[
                RuntimeServiceSpec(
                    name="dn42-router-netns",
                    role=ServiceRole.ROUTER_NETNS,
                    build=BuildSpec(target="netns"),
                    command=["sleep", "infinity"],
                    cap_add=["NET_ADMIN", "NET_RAW"],
                    devices=["/dev/net/tun:/dev/net/tun"],
                    sysctls=ROUTER_SYSCTLS,
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
                RuntimeServiceSpec(
                    name="dn42-dns",
                    role=ServiceRole.DNS,
                    image="coredns/coredns:1.12.1",
                    command=["-conf", "/etc/coredns/Corefile"],
                    network_mode="service:dn42-router-netns",
                    volumes=[VolumeMount(source="coredns", target="/etc/coredns")],
                    depends_on=["dn42-router-netns", "dn42-wg-gateway"],
                ),
            ],
        ),
        bird=Bird2ConfigSpec(
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            large_communities=BgpLargeCommunitySpec(origin_node_id=62),
            dn42_ratelimit=15,
            internal_topology=InternalTopologySpec(
                routers=["edge1", "edge2"],
                hosts={
                    "edge1": BirdHostSpec(
                        ownip="172.20.0.62",
                        ownip6="fdce:1111:2222:9500::1",
                    ),
                    "edge2": BirdHostSpec(
                        ownip="198.18.1.3",
                        ownip6="fdce:1111:2222:ff01::3",
                    ),
                },
                igp_adjacencies=[IgpAdjacencySpec(node="edge2", cost=10)],
            ),
        ),
        interfaces=[
            InterfaceSpec(
                name="dn42-lo",
                kind=InterfaceKind.DUMMY,
                mtu=None,
                addresses=[
                    "172.20.0.62/32",
                    "172.20.0.20/32",
                    "172.20.0.22/32",
                    "fdce:1111:2222:9500::1/128",
                    "fdce:1111:2222::20/128",
                    "fdce:1111:2222::22/128",
                ],
            ),
            InterfaceSpec(
                name="as4242420001",
                kind=InterfaceKind.WIREGUARD,
                private_key_ref="secret://nodes/edge1/wireguard/as4242420001/private-key",
                addresses=["172.20.0.62/32", "fdce:1111:2222:9500::1/128"],
                peer_routes=["172.20.0.105/32", "fdce:1111:2222:dead::11/128"],
                wireguard_peer=WireGuardPeerSpec(
                    public_key="+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=",
                    allowed_ips=["0.0.0.0/0", "::/0"],
                ),
            ),
            InterfaceSpec(
                name="igp-edge2",
                kind=InterfaceKind.WIREGUARD,
                private_key_ref="secret://nodes/edge1/wireguard/igp-edge2/private-key",
                addresses=["198.18.1.2/31", "fdce:1111:2222:ff01::2/127"],
                peer_routes=["198.18.1.3/32", "fdce:1111:2222:ff01::3/128"],
                wireguard_peer=WireGuardPeerSpec(
                    public_key="+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=",
                    allowed_ips=["198.18.1.3/32", "fdce:1111:2222:ff01::3/128"],
                ),
            ),
        ],
        bgp_sessions=[
            BgpSessionSpec(
                name="demopeer_4242420001_ex01_v4",
                remote_asn=4242420001,
                neighbor="172.20.0.105",
                source_address="172.20.0.62",
                address_family=AddressFamily.IPV4,
                interface="as4242420001",
                bfd=BfdSpec(),
            ),
            BgpSessionSpec(
                name="demopeer_4242420001_ex01_v6",
                remote_asn=4242420001,
                neighbor="fdce:1111:2222:dead::11",
                source_address="fdce:1111:2222:9500::1",
                address_family=AddressFamily.IPV6,
                interface="as4242420001",
                bfd=BfdSpec(),
            ),
        ],
        dns=DnsSpec(
            bind_addresses=[
                "172.20.0.20",
                "172.20.0.22",
                "fdce:1111:2222::20",
                "fdce:1111:2222::22",
            ],
            zones=[
                DnsZoneSpec(zone="example.dn42", records_ref="zone://example.dn42"),
                DnsZoneSpec(zone="0.20.172.in-addr.arpa", records_ref="zone://0.20.172"),
            ],
            forwards=[
                DnsForwardSpec(zone="dn42", upstreams=["172.20.0.53"]),
                DnsForwardSpec(zone="20.172.in-addr.arpa", upstreams=["172.20.0.53"]),
            ],
        ),
        lookglass=LookglassSpec(
            frontend_enabled=True,
            allowed_ips=["10.254.42.0/24"],
            published_frontend_ports=["5000:5000"],
            title_brand="DN42 looking glass",
            navbar_brand="DN42",
        ),
        templates=TemplateSetSpec(),
    )


__all__ = ["build_hkg1_example_state", "build_local_three_node_states"]


# --- 多节点黄金样本（本地三节点 lab，含一个虚拟 eBGP peer）---------------------

_LAB_ROUTER_BASE_IMAGE = "debian:13-slim"
_LAB_DEBIAN_MIRROR = "deb.debian.org"
_LAB_HOST_ENDPOINT = "host.docker.internal"
_LAB_ASN = 4242420000
_LAB_EBGP_ASN = 4242420002
_LAB_PREFIX4 = "172.20.0.0/26"
_LAB_PREFIX6 = "fdce:1111:2222::/48"
_LAB_EBGP_PREFIX4 = "172.20.1.0/26"
_LAB_EBGP_PREFIX6 = "fdce:3333:4444::/48"


@dataclass(frozen=True, slots=True)
class _LabNode:
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
    igp_host_ports: dict[str, int] = field(default_factory=dict)
    ebgp_host_ports: dict[str, int] = field(default_factory=dict)
    lookglass_frontend_port: int | None = None
    router_base_image: str = _LAB_ROUTER_BASE_IMAGE
    debian_mirror: str = _LAB_DEBIAN_MIRROR


@dataclass(frozen=True, slots=True)
class _LabLink:
    left: str
    right: str
    port: int
    left_v4: str
    right_v4: str
    left_v6: str
    right_v6: str
    cost: int


@dataclass(frozen=True, slots=True)
class _LabEbgpLink:
    internal_node_id: str
    port: int
    internal_v4: str
    external_v4: str
    internal_v6: str
    external_v6: str


_LAB_NODES = {
    "edge1": _LabNode(
        "edge1",
        "hkg1",
        "hkg1",
        "10.254.45.0/24",
        "10.254.45.1",
        "10.254.45.10",
        "172.20.0.62",
        "fdce:1111:2222:9500::1",
        "fe80::202:62",
        "Z880QqxvK4PEyBSglz+lBqfieuUtm1j+/Jh9JiRTenk=",
        "ZlwS4fpFyiMeaIvV//D8nTYJlX61uRaoOdmPjSbM6QY=",
        igp_host_ports={"edge2": 32001, "edge3": 32003},
        ebgp_host_ports={"extpeer": 32129},
        lookglass_frontend_port=5000,
    ),
    "edge2": _LabNode(
        "edge2",
        "hk2",
        "hk2",
        "10.254.46.0/24",
        "10.254.46.1",
        "10.254.46.10",
        "172.20.0.63",
        "fdce:1111:2222:9501::1",
        "fe80::202:63",
        "PWOvDKXDilTsKTG/hChb3gtQBZVyx93hNgN6DROceFw=",
        "+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ=",
        igp_host_ports={"edge1": 32002, "edge3": 32005},
        ebgp_host_ports={"extpeer": 32130},
    ),
    "edge3": _LabNode(
        "edge3",
        "tyo1",
        "tyo1",
        "10.254.47.0/24",
        "10.254.47.1",
        "10.254.47.10",
        "172.20.0.61",
        "fdce:1111:2222:9502::1",
        "fe80::202:61",
        "1cMSNzvHowfdJqJfvtPxUWz6mzva+as/R5hTW0BZ2zM=",
        "dTaeBq6RslNTBmhf+1dcSlK+qFVslVZfhCcdOkW41HM=",
        igp_host_ports={"edge1": 32004, "edge2": 32006},
        ebgp_host_ports={"extpeer": 32131},
    ),
}

_LAB_EBGP_NODE = _LabNode(
    "extpeer",
    "ext1",
    "ext1",
    "10.254.48.0/24",
    "10.254.48.1",
    "10.254.48.10",
    "172.20.1.1",
    "fdce:3333:4444::1",
    "fe80::715:29",
    "BnwFvdpglpMZ/jtPF+JNxhRNn/VGsnIg4X9b+pQA+i0=",
    "YAXcjFd6X26WrFRfcKv2g8RV0r24rKNijQ9gBKaWFmc=",
    igp_host_ports={},
    ebgp_host_ports={"edge1": 32229, "edge2": 32230, "edge3": 32231},
)

_LAB_LINKS = [
    _LabLink("edge1", "edge2", 30001, "198.18.10.0", "198.18.10.1", "fdce:1111:2222:ff10::0", "fdce:1111:2222:ff10::1", 10),
    _LabLink("edge1", "edge3", 30002, "198.18.10.2", "198.18.10.3", "fdce:1111:2222:ff11::0", "fdce:1111:2222:ff11::1", 15),
    _LabLink("edge2", "edge3", 30003, "198.18.10.4", "198.18.10.5", "fdce:1111:2222:ff12::0", "fdce:1111:2222:ff12::1", 20),
]

_LAB_EBGP_LINKS = {
    "edge1": _LabEbgpLink("edge1", 31029, "198.18.20.0", "198.18.20.1", "fdce:1111:2222:ef29::0", "fdce:1111:2222:ef29::1"),
    "edge2": _LabEbgpLink("edge2", 31030, "198.18.20.2", "198.18.20.3", "fdce:1111:2222:ef2a::0", "fdce:1111:2222:ef2a::1"),
    "edge3": _LabEbgpLink("edge3", 31031, "198.18.20.4", "198.18.20.5", "fdce:1111:2222:ef2b::0", "fdce:1111:2222:ef2b::1"),
}


def _lab_runtime_services(node: _LabNode) -> list[RuntimeServiceSpec]:
    return [
        RuntimeServiceSpec(
            name="dn42-router-netns",
            role=ServiceRole.ROUTER_NETNS,
            build=BuildSpec(target="netns"),
            command=["sleep", "infinity"],
            cap_add=["NET_ADMIN", "NET_RAW"],
            devices=["/dev/net/tun:/dev/net/tun"],
            ports=_lab_published_wireguard_ports(node),
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


def _lab_router_dockerfile(node: _LabNode) -> RouterDockerfileSpec:
    return RouterDockerfileSpec(
        base_image=node.router_base_image,
        debian_mirror=node.debian_mirror,
    )


def _lab_node_links(node_id: str) -> list[_LabLink]:
    return [link for link in _LAB_LINKS if node_id in {link.left, link.right}]


def _lab_peer_id(link: _LabLink, node_id: str) -> str:
    return link.right if link.left == node_id else link.left


def _lab_link_ips(link: _LabLink, node_id: str) -> tuple[str, str, str, str]:
    if link.left == node_id:
        return link.left_v4, link.right_v4, link.left_v6, link.right_v6
    return link.right_v4, link.left_v4, link.right_v6, link.left_v6


def _lab_igp_host_port(node: _LabNode, peer_node_id: str) -> int:
    return node.igp_host_ports[peer_node_id]


def _lab_ebgp_host_port(node: _LabNode, peer_node_id: str) -> int:
    return node.ebgp_host_ports[peer_node_id]


def _lab_published_wireguard_ports(node: _LabNode) -> list[PortPublishSpec]:
    ports: list[PortPublishSpec] = []
    for link in _lab_node_links(node.node_id):
        ports.append(
            PortPublishSpec(
                host_port=_lab_igp_host_port(node, _lab_peer_id(link, node.node_id)),
                container_port=link.port,
                protocol="udp",
            )
        )
    for link in _lab_ebgp_links_for_node(node.node_id):
        peer_node_id = _LAB_EBGP_NODE.node_id if node.node_id != _LAB_EBGP_NODE.node_id else link.internal_node_id
        ports.append(
            PortPublishSpec(
                host_port=_lab_ebgp_host_port(node, peer_node_id),
                container_port=link.port,
                protocol="udp",
            )
        )
    return sorted(ports, key=lambda p: (p.host_port or 0, p.container_port))


def _lab_internal_topology(node_id: str) -> InternalTopologySpec:
    return InternalTopologySpec(
        routers=list(_LAB_NODES),
        hosts={
            name: BirdHostSpec(ownip=node.router_id, ownip6=node.loopback_ipv6)
            for name, node in _LAB_NODES.items()
        },
        igp_adjacencies=[
            IgpAdjacencySpec(node=_lab_peer_id(link, node_id), cost=link.cost)
            for link in _lab_node_links(node_id)
        ],
    )


def _lab_wg_interface(node: _LabNode, link: _LabLink) -> InterfaceSpec:
    peer = _LAB_NODES[_lab_peer_id(link, node.node_id)]
    local_v4, peer_v4, local_v6, peer_v6 = _lab_link_ips(link, node.node_id)
    return InterfaceSpec(
        name=f"igp-{peer.node_id}",
        kind=InterfaceKind.WIREGUARD,
        mtu=1420,
        listen_port=link.port,
        private_key_ref=node.private_key,
        addresses=[f"{local_v4}/31", f"{local_v6}/127", f"{node.link_local_ipv6}/64"],
        peer_routes=[f"{peer_v4}/32", f"{peer_v6}/128", f"{peer.link_local_ipv6}/128"],
        wireguard_peer=WireGuardPeerSpec(
            public_key=peer.public_key,
            endpoint=f"{_LAB_HOST_ENDPOINT}:{_lab_igp_host_port(peer, node.node_id)}",
            allowed_ips=["0.0.0.0/0", "::/0"],
            persistent_keepalive_seconds=5,
        ),
    )


def _lab_ebgp_links_for_node(node_id: str) -> list[_LabEbgpLink]:
    if node_id == _LAB_EBGP_NODE.node_id:
        return list(_LAB_EBGP_LINKS.values())
    link = _LAB_EBGP_LINKS.get(node_id)
    return [link] if link else []


def _lab_ebgp_interfaces(node: _LabNode) -> list[InterfaceSpec]:
    interfaces: list[InterfaceSpec] = []
    for link in _lab_ebgp_links_for_node(node.node_id):
        internal_node = _LAB_NODES[link.internal_node_id]
        is_internal_side = node.node_id != _LAB_EBGP_NODE.node_id
        peer = _LAB_EBGP_NODE if is_internal_side else internal_node
        local_v4 = link.internal_v4 if is_internal_side else link.external_v4
        peer_v4 = link.external_v4 if is_internal_side else link.internal_v4
        local_v6 = link.internal_v6 if is_internal_side else link.external_v6
        peer_v6 = link.external_v6 if is_internal_side else link.internal_v6
        interface_name = "as4242420002" if is_internal_side else f"as0028-{internal_node.prefix}"
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
                    endpoint=f"{_LAB_HOST_ENDPOINT}:{_lab_ebgp_host_port(peer, node.node_id)}",
                    allowed_ips=["0.0.0.0/0", "::/0"],
                    persistent_keepalive_seconds=5,
                ),
            )
        )
    return interfaces


def _lab_ebgp_sessions(node: _LabNode) -> list[BgpSessionSpec]:
    sessions: list[BgpSessionSpec] = []
    is_internal_side = node.node_id != _LAB_EBGP_NODE.node_id
    for link in _lab_ebgp_links_for_node(node.node_id):
        internal_node = _LAB_NODES[link.internal_node_id]
        interface = "as4242420002" if is_internal_side else f"as0028-{internal_node.prefix}"
        remote_asn = _LAB_EBGP_ASN if is_internal_side else _LAB_ASN
        peer_v4 = link.external_v4 if is_internal_side else link.internal_v4
        peer_v6 = link.external_v6 if is_internal_side else link.internal_v6
        local_v4 = link.internal_v4 if is_internal_side else link.external_v4
        local_v6 = link.internal_v6 if is_internal_side else link.external_v6
        suffix = "_ext1" if is_internal_side else f"_{internal_node.prefix}"
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


def _lab_lookglass(node: _LabNode) -> LookglassSpec | None:
    if node.node_id == "edge1":
        return LookglassSpec(
            frontend_enabled=True,
            allowed_ips=[node.underlay_subnet],
            published_frontend_ports=[f"{node.lookglass_frontend_port}:5000"],
            title_brand="Local DN42 lab",
            navbar_brand="DN42 Lab",
        )
    return None


def _lab_build_state(node: _LabNode) -> DesiredState:
    interfaces = [
        InterfaceSpec(
            name="dn42-lo",
            kind=InterfaceKind.DUMMY,
            mtu=None,
            addresses=[f"{node.router_id}/32", f"{node.loopback_ipv6}/128"],
        ),
        *(_lab_wg_interface(node, link) for link in _lab_node_links(node.node_id)),
    ]
    interfaces.extend(_lab_ebgp_interfaces(node))
    sessions = _lab_ebgp_sessions(node)
    lookglass = _lab_lookglass(node)

    return DesiredState(
        generation=1,
        node=NodeSpec(
            node_id=node.node_id,
            site="hkg",
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            asn=_LAB_ASN,
            router_id=node.router_id,
            ipv4_prefixes=[_LAB_PREFIX4],
            ipv6_prefixes=[_LAB_PREFIX6],
            loopback_ipv4=node.router_id,
            loopback_ipv6=node.loopback_ipv6,
        ),
        runtime=RouterRuntimeSpec(
            underlay=UnderlayNetworkSpec(subnet=node.underlay_subnet, gateway=node.underlay_gateway),
            router_dockerfile=_lab_router_dockerfile(node),
            rpki=RpkiSpec(listen_host=node.rpki_ip),
            services=_lab_runtime_services(node),
        ),
        bird=Bird2ConfigSpec(
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            internal_topology=_lab_internal_topology(node.node_id),
            static_routes4=[f'{node.router_id}/32 via "dn42-lo"'],
            static_routes6=[f'{node.loopback_ipv6}/128 via "dn42-lo"'],
        ),
        interfaces=interfaces,
        bgp_sessions=sessions,
        dns=None,
        lookglass=lookglass,
        templates=TemplateSetSpec(),
    )


def _lab_build_ebgp_state() -> DesiredState:
    node = _LAB_EBGP_NODE
    return DesiredState(
        generation=1,
        node=NodeSpec(
            node_id=node.node_id,
            site="lab",
            region=Dn42OriginRegionCommunity.ASIA_EAST,
            asn=_LAB_EBGP_ASN,
            router_id=node.router_id,
            ipv4_prefixes=[_LAB_EBGP_PREFIX4],
            ipv6_prefixes=[_LAB_EBGP_PREFIX6],
            loopback_ipv4=node.router_id,
            loopback_ipv6=node.loopback_ipv6,
        ),
        runtime=RouterRuntimeSpec(
            underlay=UnderlayNetworkSpec(subnet=node.underlay_subnet, gateway=node.underlay_gateway),
            router_dockerfile=_lab_router_dockerfile(_LAB_EBGP_NODE),
            rpki=RpkiSpec(listen_host=node.rpki_ip),
            services=_lab_runtime_services(node),
        ),
        bird=Bird2ConfigSpec(
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
            *_lab_ebgp_interfaces(node),
        ],
        bgp_sessions=_lab_ebgp_sessions(node),
        dns=None,
        templates=TemplateSetSpec(),
    )


def build_local_three_node_states() -> list[tuple[str, DesiredState]]:
    """返回本地三节点 lab 的所有 `DesiredState`。

    返回 `(directory, state)` 列表，顺序为三个 AS 内部路由器（hkg1 / hk2 /
    tyo1）加上一个虚拟 eBGP peer（ext1）。`tmp/local-three-node/` 由
    `scripts/dev/render-local-three-node.py` 调用本函数渲染而成。
    """

    states = [(node.directory, _lab_build_state(node)) for node in _LAB_NODES.values()]
    states.append((_LAB_EBGP_NODE.directory, _lab_build_ebgp_state()))
    return states