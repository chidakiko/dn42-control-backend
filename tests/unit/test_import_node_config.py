from __future__ import annotations

"""``scripts/tools/import_node_config.py`` 解析器的单元测试。

用一个最小的合成节点配置目录覆盖关键解析路径：节点身份、dummy/wireguard
接口、apply 脚本里的 shell 变量展开、peer→interface 关联（链路本地 %zone /
peer-route 反查）、BFD、地址族与 internal 策略。不依赖任何外部数据目录。
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "scripts" / "tools" / "import_node_config.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("import_node_config", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_node_config"] = module
    spec.loader.exec_module(module)
    return module


importer = _load_module()


_BIRD_CONF = """\
define OWNAS = 4242420000;
define OWNIP = 172.20.0.62;
define OWNIPv6 = fdce:1111:2222:9500::1;
define OWNNET = 172.20.0.0/26;
define OWNNETv6 = fdce:1111:2222::/48;
"""

_APPLY_LO = """\
#!/usr/bin/env bash
IF="dn42-lo"
LO_V4="172.20.0.62"
DNS_V4="172.20.0.20"
ip link add dev "$IF" type dummy
ip addr replace "${LO_V4}/32" dev "$IF"
ip addr replace "${DNS_V4}/32" dev "$IF"
"""

_APPLY_KIOUBIT = """\
#!/usr/bin/env bash
IF="as4242420001"
LOCAL_V4="172.20.0.62"
PEER_V4="172.20.0.105"
ip link add dev "$IF" type wireguard
ip addr replace "${LOCAL_V4}/32" peer "${PEER_V4}/32" dev "$IF"
ip route replace "${PEER_V4}/32" dev "$IF"
"""

_APPLY_EXPLORO = """\
#!/usr/bin/env bash
IF="as4242421771"
LOCAL_LL="fe80::28"
ip link add dev "$IF" type wireguard
ip -6 addr replace "${LOCAL_LL}/64" dev "$IF"
"""

_APPLY_IGP = """\
#!/usr/bin/env bash
IF="wg-hk2"
LOCAL_V4="198.18.1.2"
PEER_V4="198.18.1.3"
ip link add dev "$IF" type wireguard
ip addr replace "${LOCAL_V4}/31" dev "$IF"
ip route replace "${PEER_V4}/32" dev "$IF"
"""

# 来自黄金样本的合法 WireGuard 密钥对。
_PUBKEY = "+aFW7xRRTwOZ6w0EmrvqN4ng2QcFA0/9Wdu9GkdwJgQ="
_PRIVKEY = "Z880QqxvK4PEyBSglz+lBqfieuUtm1j+/Jh9JiRTenk="

_WG_KIOUBIT = f"""\
[Interface]
PrivateKey = {_PRIVKEY}
ListenPort = 51810

[Peer]
PublicKey = {_PUBKEY}
Endpoint = hk1.g-load.eu:20028
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""

_WG_EXPLORO = f"""\
[Interface]
PrivateKey = {_PRIVKEY}
ListenPort = 51829

[Peer]
PublicKey = {_PUBKEY}
# Endpoint = commented.example:1234
AllowedIPs = 0.0.0.0/0, ::/0
"""

_WG_IGP = f"""\
[Interface]
PrivateKey = {_PRIVKEY}
ListenPort = 51820

[Peer]
PublicKey = {_PUBKEY}
AllowedIPs = 0.0.0.0/0, ::/0
"""

_PEER_KIOUBIT = """\
protocol bgp demopeer_4242420001_ex01_v4 from dnpeers {
    neighbor 172.20.0.105 as 4242420001;
    source address 172.20.0.62;
    bfd on;
    bfd { interval 1000 ms; multiplier 5; };
}
"""

_PEER_EXPLORO = """\
protocol bgp exploro_4242421771_ex01_v6_v4 from dnpeers {
    neighbor fe80::1771%as4242421771 as 4242421771;
    source address fe80::28;
    enable extended messages on;
    ipv4 { extended next hop on; };
}
"""

_PEER_IBGP = """\
protocol bgp ibgp_hk2_v4 {
    local as 4242420000;
    neighbor 198.18.1.3 as 4242420000;
    source address 198.18.1.2;
    rr client;
    ipv4 { import all; export all; next hop self; };
}
"""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    (tmp_path / "bird").mkdir()
    (tmp_path / "bird" / "bird.conf").write_text(_BIRD_CONF, encoding="utf-8")

    peers = tmp_path / "bird" / "peers"
    peers.mkdir()
    (peers / "demopeer.conf").write_text(_PEER_KIOUBIT, encoding="utf-8")
    (peers / "exploro.conf").write_text(_PEER_EXPLORO, encoding="utf-8")
    (peers / "ibgp.conf").write_text(_PEER_IBGP, encoding="utf-8")

    wg = tmp_path / "wireguard"
    wg.mkdir()
    (wg / "as4242420001.conf").write_text(_WG_KIOUBIT, encoding="utf-8")
    (wg / "as4242421771.conf").write_text(_WG_EXPLORO, encoding="utf-8")
    (wg / "wg-hk2.conf").write_text(_WG_IGP, encoding="utf-8")

    apply = tmp_path / "scripts" / "wg"
    apply.mkdir(parents=True)
    (apply / "apply-dn42-lo.sh").write_text(_APPLY_LO, encoding="utf-8")
    (apply / "apply-demopeer.sh").write_text(_APPLY_KIOUBIT, encoding="utf-8")
    (apply / "apply-exploro.sh").write_text(_APPLY_EXPLORO, encoding="utf-8")
    (apply / "apply-igp.sh").write_text(_APPLY_IGP, encoding="utf-8")
    return tmp_path


def test_node_identity_parsed(config_dir: Path) -> None:
    state = importer.build_state_from_config_dir(config_dir, node_id="edge1", site="hkg")

    assert state.node.node_id == "edge1"
    assert state.node.asn == 4242420000
    assert state.node.router_id == "172.20.0.62"
    assert state.node.ipv4_prefixes == ["172.20.0.0/26"]
    assert state.node.ipv6_prefixes == ["fdce:1111:2222::/48"]
    assert state.node.loopback_ipv4 == "172.20.0.62"
    assert state.node.loopback_ipv6 == "fdce:1111:2222:9500::1"
    # origin_node_id 由 loopback 末位推导。
    assert state.bird.large_communities.origin_node_id == 62


def test_interfaces_parsed(config_dir: Path) -> None:
    state = importer.build_state_from_config_dir(config_dir, node_id="edge1", site="hkg")
    by_name = {iface.name: iface for iface in state.interfaces}

    # dummy loopback：shell 变量已展开，地址完整。
    lo = by_name["dn42-lo"]
    assert lo.kind.value == "dummy"
    assert lo.mtu is None
    assert lo.addresses == ["172.20.0.62/32", "172.20.0.20/32"]
    # loopback 排在最前。
    assert state.interfaces[0].name == "dn42-lo"

    demopeer = by_name["as4242420001"]
    assert demopeer.kind.value == "wireguard"
    assert demopeer.listen_port == 51810
    assert demopeer.addresses == ["172.20.0.62/32"]
    assert demopeer.peer_routes == ["172.20.0.105/32"]
    assert demopeer.private_key_ref == _PRIVKEY
    assert demopeer.wireguard_peer is not None
    assert demopeer.wireguard_peer.public_key == _PUBKEY
    assert demopeer.wireguard_peer.endpoint == "hk1.g-load.eu:20028"
    assert demopeer.wireguard_peer.persistent_keepalive_seconds == 25

    # 被注释的 Endpoint 不应被采纳。
    exploro = by_name["as4242421771"]
    assert exploro.addresses == ["fe80::28/64"]
    assert exploro.wireguard_peer is not None
    assert exploro.wireguard_peer.endpoint is None


def test_sessions_and_interface_association(config_dir: Path) -> None:
    state = importer.build_state_from_config_dir(config_dir, node_id="edge1", site="hkg")
    by_name = {s.name: s for s in state.bgp_sessions}

    # eBGP v4：通过 peer-route 反查关联到 wireguard 接口；BFD 解析正确。
    demopeer = by_name["demopeer_4242420001_ex01_v4"]
    assert demopeer.remote_asn == 4242420001
    assert demopeer.interface == "as4242420001"
    assert demopeer.policy == "dnpeers"
    assert demopeer.address_family.value == "ipv4"
    assert demopeer.bfd is not None
    assert demopeer.bfd.interval_ms == 1000
    assert demopeer.bfd.multiplier == 5

    # 链路本地 _v6_v4：%zone 即接口；mp-bgp + extended next hop。
    exploro = by_name["exploro_4242421771_ex01_v6_v4"]
    assert exploro.interface == "as4242421771"
    assert exploro.address_family.value == "mp-bgp"
    assert exploro.extended_next_hop is True
    assert exploro.bfd is None

    # iBGP：同 ASN -> internal 策略 + rr client，且关联到内部 wg 接口。
    ibgp = by_name["ibgp_hk2_v4"]
    assert ibgp.policy == "internal"
    assert ibgp.route_reflector_client is True
    assert ibgp.interface == "wg-hk2"


def test_dry_run_state_is_valid_and_round_trips(config_dir: Path) -> None:
    state = importer.build_state_from_config_dir(config_dir, node_id="edge1", site="hkg")
    # 重新校验一遍（model_validate 会跑全部跨字段约束）。
    from dn42_schemas import DesiredState

    reparsed = DesiredState.model_validate(state.model_dump(mode="json"))
    assert len(reparsed.interfaces) == 4
    assert len(reparsed.bgp_sessions) == 3
