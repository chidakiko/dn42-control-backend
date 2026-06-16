from __future__ import annotations

"""BIRD、BGP、IGP 与 large community 相关的 schema。"""

from typing import Literal

from dn42_common import (
    Dn42OriginRegionCommunity,
    is_ipv6_link_local,
    split_ipv6_zone,
    validate_ip_address,
    validate_ip_network,
)
from pydantic import Field, field_validator, model_validator

from .base import StrictModel
from .enums import AddressFamily

# BIRD `import limit ... action <x>`：超过前缀上限后的动作。
# block=丢弃多余前缀但保持会话（防灌表/防震荡首选，不 flap）；
# restart/disable=拆/关会话；warn=只告警。
ImportLimitAction = Literal["block", "restart", "disable", "warn"]


class BfdSpec(StrictModel):
    """BFD 参数定义。

    Attributes:
        enabled: 是否为该邻居启用 BFD。
        interval_ms: BFD 控制报文发送间隔，单位为毫秒。
        multiplier: 连续丢失多少个 BFD 报文后判定邻居失活。
    """

    enabled: bool = True
    interval_ms: int = Field(default=1000, ge=50)
    multiplier: int = Field(default=5, ge=1)


class BgpSessionSpec(StrictModel):
    """单个 BGP 邻居会话定义。

    Attributes:
        name: 会话逻辑名称，要求在同一节点内唯一。
        remote_asn: 对端 ASN。
        neighbor: 对端邻居地址，可以是纯 IP，也可以是 `IPv6%iface` 这种带 zone 的形式。
        source_address: 本端用于建立会话的源地址。
        address_family: 该会话承载的地址族，例如 IPv4、IPv6 或 MP-BGP。
        interface: 邻居所属接口名；对链路本地 IPv6 或模板生成 protocol 名称时尤其重要。
        policy: 传给模板层的策略名称，常见值如 `dnpeers`、`internal`。
        import_mode: 模板层使用的导入模式。
        export_mode: 模板层使用的导出模式。
        protocol_suffix: 追加到模板生成 protocol 名后的后缀，便于区分多个同 ASN 邻居。
        extended_next_hop: 是否启用 extended next hop。
        bfd: 该 BGP 会话对应的 BFD 参数；为 `None` 时表示不生成 BFD 配置。
        route_reflector_client: 是否把该对端视为 RR client。
        import_limit: 本会话导入前缀上限（per channel）覆盖；`None` 用节点级默认。
        import_limit_action: 超限动作覆盖；`None` 用节点级默认。
        enabled: 当前会话是否参与最终渲染。
    """

    name: str
    remote_asn: int = Field(ge=1)
    neighbor: str
    source_address: str
    address_family: AddressFamily
    interface: str | None = None
    policy: str = "dnpeers"
    import_mode: str = "filter"
    export_mode: str = "filter"
    protocol_suffix: str = ""
    extended_next_hop: bool = False
    bfd: BfdSpec | None = Field(default_factory=BfdSpec)
    route_reflector_client: bool = False
    # 本会话的导入前缀上限覆盖：``None`` 表示沿用节点级
    # ``Bird2ConfigSpec.import_limit`` / ``import_limit_action``。
    import_limit: int | None = Field(default=None, ge=1)
    import_limit_action: ImportLimitAction | None = None
    enabled: bool = True

    @field_validator("source_address")
    @classmethod
    def validate_source_address(cls, value: str) -> str:
        validate_ip_address(value)
        return value

    @model_validator(mode="after")
    def validate_neighbor(self) -> "BgpSessionSpec":
        address, zone = split_ipv6_zone(self.neighbor)
        validate_ip_address(address)
        if zone is not None and self.interface and self.interface != zone:
            raise ValueError("neighbor zone and interface must match")
        if is_ipv6_link_local(address) and zone is None and not self.interface:
            raise ValueError(
                "IPv6 link-local neighbor requires either a '%zone' suffix "
                "or an explicit 'interface' field"
            )
        return self

    def is_internal(self, own_asn: int) -> bool:
        """判断该会话相对于本节点 ASN 是否应被视为内部会话。"""

        return self.remote_asn == own_asn or self.policy == "internal"


class BirdHostSpec(StrictModel):
    """internal topology 中单个节点的路由主机视图。

    Attributes:
        ownip: 节点 loopback IPv4 或用于 iBGP 标识的 IPv4 地址。
        ownip6: 节点 loopback IPv6 或用于 iBGP 标识的 IPv6 地址。
        ibgp_rr_upstreams: 当节点不是 full-mesh iBGP，而是走 RR 拓扑时，列出其上游 RR 节点名。
    """

    ownip: str
    ownip6: str
    ibgp_rr_upstreams: list[str] = Field(default_factory=list)

    @field_validator("ownip", "ownip6")
    @classmethod
    def validate_ip(cls, value: str) -> str:
        validate_ip_address(value)
        return value


class IgpAdjacencySpec(StrictModel):
    """单条 IGP 邻接关系定义。

    Attributes:
        node: 对端节点名，必须能在 topology 的 hostvars 中找到。
        cost: 到该邻居的 IGP 开销；为 `None` 时交给模板默认值处理。
        interface: 显式指定承载该邻接的接口名。
        iface_type: IGP 接口类型，默认是点到点 `ptp`。
    """

    node: str
    cost: int | None = Field(default=None, ge=1)
    interface: str | None = None
    iface_type: str = "ptp"


class DummyInterfaceSpec(StrictModel):
    """供 BIRD 模板引用的 dummy 接口定义。

    Attributes:
        ifname: 需要出现在模板中的接口名。
        track_service: 为 `True` 时表示该接口承载任播/服务地址，应进入 direct protocol；
            为 `False` 时表示该接口只作为 stub 接口处理。
    """

    ifname: str
    track_service: bool = False


class InternalTopologySpec(StrictModel):
    """AS 内部拓扑视图。

    这部分信息主要用于驱动模板层生成 OSPF 与 iBGP 相关配置。

    Attributes:
        routers: 参与内部路由域的正常路由器节点名列表。
        private_nodes: 仅在内部可见、但不一定参与正常对外宣告的私有节点列表。
        hosts: 节点名到 `BirdHostSpec` 的映射，是模板层生成内部 iBGP/OSPF 邻居信息的主要来源。
        igp_adjacencies: 显式 IGP 邻接列表；用于非全连接链路场景下描述谁和谁直连。
        full_mesh_ibgp: 是否默认在 `routers` 之间建立 full-mesh iBGP。
        ospf_v2: 是否生成 OSPFv2 相关配置。
        ospf_v3: 是否生成 OSPFv3 相关配置。
    """

    routers: list[str]
    private_nodes: list[str] = Field(default_factory=list)
    hosts: dict[str, BirdHostSpec]
    igp_adjacencies: list[IgpAdjacencySpec] = Field(default_factory=list)
    full_mesh_ibgp: bool = True
    ospf_v2: bool = True
    ospf_v3: bool = True

    @model_validator(mode="after")
    def validate_topology_hosts(self) -> "InternalTopologySpec":
        known_hosts = set(self.hosts)
        missing_routers = sorted(set(self.routers).difference(known_hosts))
        if missing_routers:
            raise ValueError(f"internal topology routers missing hostvars: {', '.join(missing_routers)}")

        missing_private = sorted(set(self.private_nodes).difference(known_hosts))
        if missing_private:
            raise ValueError(
                f"internal topology private nodes missing hostvars: {', '.join(missing_private)}"
            )

        missing_adjacencies = sorted(
            adjacency.node for adjacency in self.igp_adjacencies if adjacency.node not in known_hosts
        )
        if missing_adjacencies:
            raise ValueError(
                "internal topology IGP adjacencies missing hostvars: "
                + ", ".join(missing_adjacencies)
            )
        return self


class BgpLargeCommunitySpec(StrictModel):
    """large community 编码相关参数。

    Attributes:
        origin_node_type: 标记来源节点 ID 所使用的社区类型编号。
        origin_region_type: 标记来源区域所使用的社区类型编号。
        policy_type: 标记策略字段所使用的社区类型编号。
        origin_node_id: 显式指定来源节点 ID；未设置时由模板层按节点信息推导。
        policy_local_pref: 对应 local-pref 语义的策略值。
        policy_deprep: 对应 de-prepend 语义的策略值。
        rejected_asns: 应被标记为拒绝来源的 ASN 列表。
    """

    origin_node_type: int = Field(default=100, ge=0, le=4294967295)
    origin_region_type: int = Field(default=101, ge=0, le=4294967295)
    policy_type: int = Field(default=102, ge=0, le=4294967295)
    origin_node_id: int | None = Field(default=None, ge=0, le=4294967295)
    policy_local_pref: int = Field(default=10, ge=0, le=4294967295)
    policy_deprep: int = Field(default=20, ge=0, le=4294967295)
    rejected_asns: list[int] = Field(default_factory=list)

    @field_validator("rejected_asns")
    @classmethod
    def validate_rejected_asns(cls, value: list[int]) -> list[int]:
        for asn in value:
            if asn < 1 or asn > 4294967295:
                raise ValueError("rejected_asns must contain valid ASNs")
        return value


class Bird2ConfigSpec(StrictModel):
    """BIRD2 模板所需的高层配置。

    这个模型聚合了模板层渲染 BIRD 配置时需要的高层输入。它既可以完全独立使用，
    也可以和 `DesiredState.interfaces`、`DesiredState.bgp_sessions`、runtime 信息一起配合，
    生成完整节点配置。

    Attributes:
        region: 当前节点所属 DN42 区域；未设置时通常回退到节点级 region。
        internal_topology: AS 内部拓扑定义；存在时模板层会优先从这里生成 OSPF 与 iBGP 关系。
        large_communities: large community 编码与策略相关参数。
        dn42_ratelimit: BIRD 模板中使用的 DN42 默认限速参数。
        import_limit: 每 eBGP 对端 per-channel 默认导入前缀上限；0 表示不限制。
        import_limit_action: 超过 import_limit 时的动作，默认 `block`（丢多余前缀、不断会话）。
        disable_ebgp: 是否在模板层整体禁用 eBGP 邻居配置生成。
        export_ownnets: 是否默认对外宣告本节点自有前缀。
        dummy_interfaces: 需要在 BIRD 中引用的 dummy 接口映射。
        stub_ifnames: 需要按 stub 接口处理的接口名列表。
        stub_ifnames_append: 附加到默认 stub 接口集合中的接口名列表。
        static_routes4: 需要注入 BIRD 的 IPv4 静态路由表达式列表。
        static_routes6: 需要注入 BIRD 的 IPv6 静态路由表达式列表。
    """

    region: Dn42OriginRegionCommunity | None = None
    internal_topology: InternalTopologySpec | None = None
    large_communities: BgpLargeCommunitySpec = Field(default_factory=BgpLargeCommunitySpec)
    dn42_ratelimit: int = Field(default=15, ge=1)
    # 每个 eBGP 对端、每 channel 的默认导入前缀上限 + 超限动作（防对端灌表/震荡）。
    # 单会话可在 ``BgpSessionSpec.import_limit`` 覆盖。``import_limit=0`` 关闭限制。
    import_limit: int = Field(default=8500, ge=0)
    import_limit_action: ImportLimitAction = "block"
    disable_ebgp: bool = False
    export_ownnets: bool = True
    dummy_interfaces: dict[str, DummyInterfaceSpec] = Field(default_factory=dict)
    stub_ifnames: list[str] = Field(default_factory=list)
    stub_ifnames_append: list[str] = Field(default_factory=list)
    static_routes4: list[str] = Field(default_factory=list)
    static_routes6: list[str] = Field(default_factory=list)

    @field_validator("static_routes4", "static_routes6")
    @classmethod
    def validate_static_routes(cls, value: list[str]) -> list[str]:
        for route in value:
            validate_ip_network(route.split(maxsplit=1)[0])
        return value

