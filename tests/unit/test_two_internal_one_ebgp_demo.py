from __future__ import annotations

"""两台同 AS内部 + 一台 eBGP 外部 peer 的演示实验渲染脚本（
``scripts/dev/render-two-internal-one-ebgp-demo.py``）的集成测试。

场景与三节点 lab 不同：仅 hkg1 与 pvg1 同属 AS4242420000，互为 iBGP；
多出的 hutao 节点在 AS4242420002，以 eBGP 接入；三个节点都接入
``frontend/`` 下的公共 looking-glass。本文件锐意锁定：

* 每个内部节点：iBGP 列表仅含另一个内部节点；只产生 1 个
  ``wireguard/igp-*`` 接口 + 1 个 ``wireguard/as4242420002.conf`` 与 hutao
  的 eBGP；wg 配置不含 ``secret://`` 占位符（表示本地 demo 里就映射为
  明文 base64 私钥）；lookglass 只启用 proxy、不启用 frontend。
* 外部 hutao 节点：与 hkg1 / pvg1 各作一条 ``as0028-*`` WireGuard 与
  ``protocol bgp ebgp_4242420000_<node>_v4`` 会话。
* ``main()`` 生成三节点 + frontend 赋仰目录与 README；frontend 的 compose
  带 ``BIRDLG_SERVERS`` 多节点描述串 (HKG1 / PVG1 / HUTAO)、发布端口 5000，
  并以 external 方式接入各节点的 underlay 网络 (``dn42-*-edge_router_underlay``)。
* README 顶部仍指向重命名后的 ``scripts/dev/render-two-internal-one-ebgp-demo.py``。
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
        assert state.lookglass is not None
        assert state.lookglass.frontend_enabled is False
        assert state.lookglass.published_frontend_ports == []

    hutao_state = lab.build_external_state()
    hutao_rendered = render_desired_state(hutao_state)
    hutao_paths = {file.path for file in hutao_rendered}
    hutao_peers = next(file.content for file in hutao_rendered if file.path == "bird/dn42_peers.conf")

    assert "wireguard/as0028-hkg1.conf" in hutao_paths
    assert "wireguard/as0028-pvg1.conf" in hutao_paths
    assert "protocol bgp ebgp_4242420000_hkg1_v4" in hutao_peers
    assert "protocol bgp ebgp_4242420000_pvg1_v4" in hutao_peers


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


def test_demo_main_renders_frontend_and_node_directories(tmp_path: Path) -> None:
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
        assert "dn42-bird-lg-proxy" in services
        assert "dn42-bird-lg" not in services

    assert {(32001, 30001, "udp"), (32129, 31029, "udp")} <= _published_ports(states["hkg1"])
    assert {(32002, 30001, "udp"), (32130, 31030, "udp")} <= _published_ports(states["pvg1"])
    assert {(32229, 31029, "udp"), (32230, 31030, "udp")} <= _published_ports(states["hutao"])

    frontend_script = (tmp_path / "frontend" / "run-frontend.sh").read_text(encoding="utf-8")
    assert "BIRDLG_SERVERS=HKG1<10.254.51.2>,PVG1<10.254.52.2>,HUTAO<10.254.53.2>" in frontend_script
    assert "BIRDLG_PROXY_PORT=8000" in frontend_script
    assert "dn42-edge1_router_underlay" in frontend_script
    assert "dn42-pvg1-edge_router_underlay" in frontend_script
    assert "dn42-hutao-peer_router_underlay" in frontend_script
    assert "-p 5000:5000" in frontend_script

    readme = (tmp_path / "README.md").read_text(encoding="utf-8")
    assert "Two internal plus one eBGP DN42 lab" in readme
    assert "docker compose" not in readme
    assert "python scripts/dev/render-two-internal-one-ebgp-demo.py" in readme