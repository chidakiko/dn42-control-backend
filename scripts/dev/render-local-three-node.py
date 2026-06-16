"""把内置三节点本地 lab 渲染到 ``tmp/local-three-node/``（开发辅助）。

``build_local_three_node_states()``（三个 AS 内部路由器 + 一个虚拟 eBGP peer）逐
节点经模板管线渲染为 BIRD / WireGuard / 脚本文件，供本地 docker-compose 起栈联调。
不参与生产。
"""

from __future__ import annotations

from pathlib import Path
import shutil
from textwrap import dedent

from dn42_runtime import write_rendered_files
from dn42_schemas.testing import build_local_three_node_states
from dn42_templates import render_desired_state

OUTPUT_DIR = Path("tmp/local-three-node")


def write_states() -> None:
    for directory, state in build_local_three_node_states():
        rendered = render_desired_state(state)
        write_rendered_files(rendered, OUTPUT_DIR / directory)


def write_readme() -> None:
    (OUTPUT_DIR / "README.md").write_text(
        dedent(
            """
            # Local three-node DN42 lab

            This lab renders four independent configuration directories: `hkg1`, `hk2`, `tyo1`, and `ext1`. Each directory contains the node's BIRD/WireGuard configuration and scripts. Container orchestration is data-driven: deploy these nodes by provisioning them into the control server database (see `scripts/dev/provision-three-node.py`) and letting node agents reconcile them via the Docker Engine API.

            The three AS-internal routers still form a full-mesh WireGuard IGP. Because all deployments run on one host, inter-node WireGuard traffic uses `host.docker.internal` plus unique published UDP ports instead of peer container IPs.

            The lab also defines `extpeer` as a virtual eBGP neighbor in AS4242420002. It announces `172.20.1.0/26` and `fdce:3333:4444::/48` to all three internal routers over dedicated WireGuard links.

            ```bash
            python scripts/dev/render-local-three-node.py
            ```

            The `hkg1` node defines a bird-lg-go frontend published on `http://127.0.0.1:5000`.
            """
        ).lstrip(),
        encoding="utf-8",
    )


def main() -> None:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)
    write_states()
    write_readme()


if __name__ == "__main__":
    main()
