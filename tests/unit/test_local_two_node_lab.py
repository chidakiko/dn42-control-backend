from __future__ import annotations

"""两节点本地 lab（``scripts/dev/render-local-two-node.py``）集成测试。

两节点 lab 是最小的 “两台 hk* 节点 + iBGP via WireGuard” 场景，本文件
锁定以下不变量：

* 脚本主函数能产出 ``docker/router/Dockerfile``（不再渲染
  docker-compose.yml），路由镜像默认以 ``debian:13-slim`` 为 base、默认
  镜像源 ``deb.debian.org``，build args 中不会出现 ``DEBIAN_MIRROR``。
* ``build_state(...)`` 接受可调 ``router_base_image`` / ``debian_mirror``
  参数，并会透传到 ``state.runtime.router_dockerfile``，以便需要时切换
  镜像源（例如 GFW 环境下的镜像加速）。
"""

import importlib.util
from pathlib import Path
import sys
from typing import Any


SCRIPT_PATH = Path("scripts/dev/render-local-two-node.py")


def load_lab_module() -> Any:
    spec = importlib.util.spec_from_file_location("render_local_two_node", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["render_local_two_node"] = module
    spec.loader.exec_module(module)
    return module


def test_two_node_lab_uses_local_router_dockerfile(tmp_path: Path) -> None:
    lab = load_lab_module()
    original_output_dir = lab.OUTPUT_DIR
    lab.OUTPUT_DIR = tmp_path
    try:
        lab.main()
    finally:
        lab.OUTPUT_DIR = original_output_dir

    # 容器编排不渲染文件。
    assert not (tmp_path / "docker-compose.yml").exists()
    dockerfile = (tmp_path / "docker" / "router" / "Dockerfile").read_text(encoding="utf-8")

    assert 'FROM debian:13-slim AS debian-base' in dockerfile
    assert 'deb.debian.org/debian-security' in dockerfile


def test_two_node_build_state_accepts_router_dockerfile_settings() -> None:
    lab = load_lab_module()

    state = lab.build_state(
        node_id="edge1",
        peer_node="edge2",
        router_id="172.20.0.62",
        loopback_ipv6="fdce:1111:2222:9500::1",
        wg_name="igp-edge2",
        private_key=lab.HKG1_PRIVATE_KEY,
        peer_public_key=lab.HK2_PUBLIC_KEY,
        endpoint="10.254.44.3:30001",
        addresses=["198.18.10.0/31", "fdce:1111:2222:ff10::0/127", "fe80::202:62/64"],
        peer_routes=["198.18.10.1/32", "fdce:1111:2222:ff10::1/128", "fe80::202:63/128"],
        router_base_image="debian:bookworm-slim",
        debian_mirror="mirror.example.invalid",
    )

    assert state.runtime.router_dockerfile.base_image == "debian:bookworm-slim"
    assert state.runtime.router_dockerfile.debian_mirror == "mirror.example.invalid"