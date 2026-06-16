"""把一个两节点本地 lab（一对 iBGP 互联的内部路由器）渲染到磁盘（开发辅助）。

就地构造 ``DesiredState`` 经模板管线渲染，供本地起栈联调 iBGP/OSPF 互联与
WireGuard 隧道。不参与生产。
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from dn42_common import Dn42OriginRegionCommunity
from dn42_runtime import render_router_dockerfile, write_rendered_files
from dn42_schemas import (
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
    RouterDockerfileSpec,
    RouterRuntimeSpec,
    RuntimeServiceSpec,
    ServiceRole,
    TemplateSetSpec,
    UnderlayNetworkSpec,
    VolumeMount,
    WireGuardPeerSpec,
)
from dn42_templates import render_desired_state

OUTPUT_DIR = Path("tmp/local-two-node")
DEFAULT_ROUTER_BASE_IMAGE = "debian:13-slim"
DEFAULT_DEBIAN_MIRROR = "deb.debian.org"
UNDERLAY_SUBNET = "10.254.44.0/24"
UNDERLAY_GATEWAY = "10.254.44.1"
RPKI_IP = "10.254.44.10"
ASN = 4242420000
ROUTER_SYSCTLS = {
  "net.ipv4.conf.all.rp_filter": "0",
  "net.ipv4.conf.default.rp_filter": "0",
  "net.ipv4.ip_forward": "1",
  "net.ipv6.conf.all.forwarding": "1",
  "net.ipv6.conf.default.forwarding": "1",
}

HKG1_PRIVATE_KEY = "Z880QqxvK4PEyBSglz+lBqfieuUtm1j+/Jh9JiRTenk="
HKG1_PUBLIC_KEY = "ZlwS4fpFyiMeaIvV//D8nTYJlX61uRaoOdmPjSbM6QY="
HK2_PRIVATE_KEY = "PWOvDKXDilTsKTG/hChb3gtQBZVyx93hNgN6DROceFw="
HK2_PUBLIC_KEY = "+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ="

HOSTS = {
    "edge1": BirdHostSpec(
        ownip="172.20.0.62",
        ownip6="fdce:1111:2222:9500::1",
    ),
    "edge2": BirdHostSpec(
        ownip="172.20.0.63",
        ownip6="fdce:1111:2222:9501::1",
    ),
}


def runtime_services() -> list[RuntimeServiceSpec]:
    return [
        RuntimeServiceSpec(
            name="dn42-router-netns",
            role=ServiceRole.ROUTER_NETNS,
          build=BuildSpec(target="netns"),
            command=["sleep", "infinity"],
            cap_add=["NET_ADMIN", "NET_RAW"],
            devices=["/dev/net/tun:/dev/net/tun"],
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
            ],
            depends_on=["dn42-router-netns", "dn42-wg-gateway"],
        ),
    ]


def topology(local_node: str, peer_node: str) -> InternalTopologySpec:
    return InternalTopologySpec(
        routers=["edge1", "edge2"],
        hosts=HOSTS,
        igp_adjacencies=[IgpAdjacencySpec(node=peer_node, cost=10)],
    )


def build_state(
    *,
    node_id: str,
    peer_node: str,
    router_id: str,
    loopback_ipv6: str,
    wg_name: str,
    private_key: str,
    peer_public_key: str,
    endpoint: str,
    addresses: list[str],
    peer_routes: list[str],
    router_base_image: str = DEFAULT_ROUTER_BASE_IMAGE,
    debian_mirror: str = DEFAULT_DEBIAN_MIRROR,
) -> DesiredState:
    return DesiredState(
        generation=1,
        node=NodeSpec(
            node_id=node_id,
            site="hkg",
          region=Dn42OriginRegionCommunity.ASIA_EAST,
            asn=ASN,
            router_id=router_id,
            ipv4_prefixes=["172.20.0.0/26"],
            ipv6_prefixes=["fdce:1111:2222::/48"],
            loopback_ipv4=router_id,
            loopback_ipv6=loopback_ipv6,
        ),
        runtime=RouterRuntimeSpec(
            underlay=UnderlayNetworkSpec(subnet=UNDERLAY_SUBNET, gateway=UNDERLAY_GATEWAY),
          router_dockerfile=RouterDockerfileSpec(
            base_image=router_base_image,
            debian_mirror=debian_mirror,
          ),
            services=runtime_services(),
        ),
        bird=Bird2ConfigSpec(
          region=Dn42OriginRegionCommunity.ASIA_EAST,
            internal_topology=topology(node_id, peer_node),
            static_routes4=[f'{router_id}/32 via "dn42-lo"'],
            static_routes6=[f'{loopback_ipv6}/128 via "dn42-lo"'],
        ),
        interfaces=[
            InterfaceSpec(
                name="dn42-lo",
                kind=InterfaceKind.DUMMY,
                mtu=None,
                addresses=[f"{router_id}/32", f"{loopback_ipv6}/128"],
            ),
            InterfaceSpec(
                name=wg_name,
                kind=InterfaceKind.WIREGUARD,
                mtu=1420,
                listen_port=30001,
                private_key_ref=private_key,
                addresses=addresses,
                peer_routes=peer_routes,
                wireguard_peer=WireGuardPeerSpec(
                    public_key=peer_public_key,
                    endpoint=endpoint,
                    allowed_ips=["0.0.0.0/0", "::/0"],
                    persistent_keepalive_seconds=5,
                ),
            ),
        ],
        dns=None,
        templates=TemplateSetSpec(),
    )


def hkg1_state() -> DesiredState:
    return build_state(
        node_id="edge1",
        peer_node="edge2",
        router_id="172.20.0.62",
        loopback_ipv6="fdce:1111:2222:9500::1",
        wg_name="igp-edge2",
        private_key=HKG1_PRIVATE_KEY,
        peer_public_key=HK2_PUBLIC_KEY,
        endpoint="10.254.44.3:30001",
        addresses=[
            "198.18.10.0/31",
            "fdce:1111:2222:ff10::0/127",
            "fe80::202:62/64",
        ],
        peer_routes=[
            "198.18.10.1/32",
            "fdce:1111:2222:ff10::1/128",
            "fe80::202:63/128",
        ],
    )


def hk2_state() -> DesiredState:
    return build_state(
        node_id="edge2",
        peer_node="edge1",
        router_id="172.20.0.63",
        loopback_ipv6="fdce:1111:2222:9501::1",
        wg_name="igp-edge1",
        private_key=HK2_PRIVATE_KEY,
        peer_public_key=HKG1_PUBLIC_KEY,
        endpoint="10.254.44.2:30001",
        addresses=[
            "198.18.10.1/31",
            "fdce:1111:2222:ff10::1/127",
            "fe80::202:63/64",
        ],
        peer_routes=[
            "198.18.10.0/32",
            "fdce:1111:2222:ff10::0/128",
            "fe80::202:62/128",
        ],
    )


def write_node(name: str, state: DesiredState) -> None:
    write_rendered_files(render_desired_state(state), OUTPUT_DIR / name)


def write_readme() -> None:
    (OUTPUT_DIR / "README.md").write_text(
        dedent(
            """
            # Local two-node DN42 lab

            This example starts two local AS-internal nodes, `edge1` and `edge2`, on a shared Docker underlay. The nodes use WireGuard for the IGP link, OSPF v2/v3 inside BIRD, and ansible-dn42-style full-mesh iBGP.

            The rendered directories contain configuration files and the shared
            router Dockerfile only. Container orchestration is data-driven:
            provision the nodes into the control server database and let node
            agents reconcile them via the Docker Engine API.

            ```bash
            python scripts/dev/render-local-two-node.py
            ```
            """
        ).lstrip(),
        encoding="utf-8",
    )


def write_router_dockerfile() -> None:
    dockerfile_path = OUTPUT_DIR / "docker" / "router" / "Dockerfile"
    dockerfile_path.parent.mkdir(parents=True, exist_ok=True)
    dockerfile_path.write_text(
    render_router_dockerfile(
      RouterDockerfileSpec(
        base_image=DEFAULT_ROUTER_BASE_IMAGE,
        debian_mirror=DEFAULT_DEBIAN_MIRROR,
      )
    ),
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_router_dockerfile()
    write_node("hkg1", hkg1_state())
    write_node("hk2", hk2_state())
    write_readme()


if __name__ == "__main__":
    main()
