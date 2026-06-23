from __future__ import annotations

"""两台同 AS内部 + 一台 eBGP 外部 peer 的演示实验渲染脚本（
``scripts/dev/render-two-internal-one-ebgp-demo.py``）的集成测试。

场景与三节点 lab 不同：仅 hkg1 与 pvg1 同属 AS4242420000，互为 iBGP；
多出的 hutao 节点在 AS4242420002，以 eBGP 接入。本文件锐意锁定：

* 每个内部节点：iBGP 列表仅含另一个内部节点；只产生 1 个
  ``wireguard/igp-*`` 接口 + 1 个 ``wireguard/as4242420002.conf`` 与 hutao
  的 eBGP；wg 配置不含 ``secret://`` 占位符（表示本地 demo 里就映射为
  明文 base64 私钥）。
* 外部 hutao 节点：与 hkg1 / pvg1 各作一条 ``as0028-*`` WireGuard 与
  ``protocol bgp ebgp_4242420000_<node>_v4`` 会话。
* ``main()`` 生成三节点目录与 README。
* README 顶部仍指向 ``scripts/dev/render-two-internal-one-ebgp-demo.py``。
"""

import importlib.util
from pathlib import Path
import sys
from typing import Any

from dn42_templates import build_config_bird2_context, render_desired_state


SCRIPT_PATH = Path("scripts/dev/render-two-internal-one-ebgp-demo.py")


def load_lab_module() -> Any:
    spec = importlib.util.spec_from_file_location("render_two_internal_one_ebgp_demo", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_two_internal_one_ebgp_demo"] = module
    spec.loader.exec_module(module)
    return module


def test_demo_renders_internal_and_ebgp_topology() -> None:
    lab = load_lab_module()

    for node in lab.NODES.values():
        state = lab.build_internal_state(node)
        context = build_config_bird2_context(state)
        rendered = render_desired_state(state)
        paths = {file.path for file in rendered}
        ibgp = next(file.content for file in rendered if file.path == "bird/ibgp.conf")
        dn42_peers = next(file.content for file in rendered if file.path == "bird/dn42_peers.conf")
        wg_configs = [file.content for file in rendered if file.path.startswith("wireguard/")]

        assert context["internal_router_names"] == ["edge1", "pvg1-edge"]
        assert [entry["peer_node"] for entry in context["ospf_neighbor_interfaces"]] == [
            lab.peer_id(lab.IGP_LINK, node.node_id)
        ]
        assert sum(path.startswith("wireguard/igp-") for path in paths) == 1
        assert "wireguard/as4242420002.conf" in paths
        assert f"protocol bgp ibgp_{lab.peer_id(lab.IGP_LINK, node.node_id).replace('-', '_')}" in ibgp
        assert "protocol bgp ebgp_4242420002_v4" in dn42_peers
        assert all("secret://" not in content for content in wg_configs)

    hutao_state = lab.build_external_state()
    hutao_rendered = render_desired_state(hutao_state)
    hutao_paths = {file.path for file in hutao_rendered}
    hutao_peers = next(file.content for file in hutao_rendered if file.path == "bird/dn42_peers.conf")

    assert "wireguard/as0028-hkg1.conf" in hutao_paths
    assert "wireguard/as0028-pvg1.conf" in hutao_paths
    assert "protocol bgp ebgp_4242420000_hkg1_v4" in hutao_peers
    assert "protocol bgp ebgp_4242420000_pvg1_v4" in hutao_peers


def test_session_local_pref_renders_default_bgp_local_pref() -> None:
    """会话设 ``local_pref`` → 渲染出 ``default bgp_local_pref N``；不设则不出现。

    这是「非对称选路」修复的渲染侧：在某入口给某 AS 的会话抬 local_pref，使该入口学到的
    前缀经 iBGP 传播后在全 fleet 胜出，去/回程走同一对等口。
    """

    lab = load_lab_module()
    node = next(iter(lab.NODES.values()))
    state = lab.build_internal_state(node)

    baseline = next(
        f.content for f in render_desired_state(state) if f.path == "bird/dn42_peers.conf"
    )
    assert "bgp_local_pref" not in baseline  # 默认不发，沿用 BIRD 默认
    assert "import where dn42_import_filter" in baseline  # 默认用 where 形式

    assert state.bgp_sessions, "demo 内部节点应至少有一条 eBGP 会话"
    bumped = state.bgp_sessions[0].model_copy(update={"local_pref": 150})
    state2 = state.model_copy(update={"bgp_sessions": [bumped, *state.bgp_sessions[1:]]})
    peers = next(
        f.content for f in render_desired_state(state2) if f.path == "bird/dn42_peers.conf"
    )
    # local_pref 用 import filter 形式落地（default bgp_local_pref 非合法 BIRD 语法）。
    assert "import filter { bgp_local_pref = 150; dn42_import_filter(" in peers


def test_community_route_tuning_blocks() -> None:
    """社区/路由调优三块：link_latency 打 latency 社区、cold_potato_med 可配、per-prefix local_pref。"""

    from dn42_schemas import RouteLocalPrefSpec

    lab = load_lab_module()
    node = next(iter(lab.NODES.values()))
    state = lab.build_internal_state(node)

    # 块1：link_latency 传进 import/export 过滤器（不再写死 0）。
    sess = state.bgp_sessions[0].model_copy(update={"link_latency": 4})
    # 块2：cold_potato_med 可配。块3：per-prefix local_pref（v4 + v6 各一）。
    bird = state.bird.model_copy(
        update={
            "cold_potato_med": 30,
            "route_local_pref": [
                RouteLocalPrefSpec(prefix="172.20.0.160/27", local_pref=200),
                RouteLocalPrefSpec(prefix="fd00:1234::/48", local_pref=150),
            ],
        }
    )
    state2 = state.model_copy(update={"bgp_sessions": [sess, *state.bgp_sessions[1:]], "bird": bird})
    rendered = {f.path: f.content for f in render_desired_state(state2)}
    peers = rendered["bird/dn42_peers.conf"]
    filters = rendered["bird/custom_filters.conf"]

    # 块1：latency=4 进 import + export。
    assert "dn42_import_filter(4,23,34)" in peers
    assert "dn42_export_filter(4,23,34)" in peers
    # 块2：cold-potato MED 配置化。
    assert "define COLD_POTATO_MED = 30;" in filters
    assert "bgp_med = COLD_POTATO_MED;" in filters
    # 块3：per-prefix local_pref，带类型守卫，v4/v6 各一。
    assert "if (net.type = NET_IP4 && net = 172.20.0.160/27) then bgp_local_pref = 200;" in filters
    assert "if (net.type = NET_IP6 && net = fd00:1234::/48) then bgp_local_pref = 150;" in filters

    # 默认（不设）时不渲染这些——不影响存量。
    base = {f.path: f.content for f in render_desired_state(state)}
    assert "bgp_local_pref =" not in base["bird/custom_filters.conf"]
    assert "dn42_import_filter(0," in base["bird/dn42_peers.conf"]


def _published_ports(state) -> set[tuple[int, int, str]]:
    published: set[tuple[int, int, str]] = set()
    for service in state.runtime.services:
        if not service.enabled:
            continue
        for port in service.ports:
            if port.host_port is None:
                continue
            container_end = port.container_port_end or port.container_port
            for offset in range(container_end - port.container_port + 1):
                published.add(
                    (port.host_port + offset, port.container_port + offset, port.protocol)
                )
    return published


def test_demo_main_renders_node_directories(tmp_path: Path) -> None:
    lab = load_lab_module()
    original_output_dir = lab.OUTPUT_DIR
    lab.OUTPUT_DIR = tmp_path
    try:
        lab.main()
    finally:
        lab.OUTPUT_DIR = original_output_dir

    states = {
        "hkg1": lab.build_internal_state(lab.NODES["edge1"]),
        "pvg1": lab.build_internal_state(lab.NODES["pvg1-edge"]),
        "hutao": lab.build_external_state(),
    }
    for directory, state in states.items():
        # 容器编排与镜像构建都不渲染文件——节点目录里只有配置。
        assert not (tmp_path / directory / "docker-compose.yml").exists()
        assert not (tmp_path / directory / "docker" / "router" / "Dockerfile").exists()

        services = {s.name for s in state.runtime.services if s.enabled}
        assert "dn42-bird-lg-proxy" not in services
        assert "dn42-bird-lg" not in services

    assert {(32001, 30001, "udp"), (32129, 31029, "udp")} <= _published_ports(states["hkg1"])
    assert {(32002, 30001, "udp"), (32130, 31030, "udp")} <= _published_ports(states["pvg1"])
    assert {(32229, 31029, "udp"), (32230, 31030, "udp")} <= _published_ports(states["hutao"])

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "Two internal plus one eBGP DN42 lab" in readme
    assert "docker compose" not in readme
    assert "python scripts/dev/render-two-internal-one-ebgp-demo.py" in readme