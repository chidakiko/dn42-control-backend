from __future__ import annotations

"""把 `DesiredState` 渲染成完整部署文件集的高层入口。"""

from dn42_runtime import RenderedFile
from dn42_schemas import DesiredState, InterfaceKind

from .bird2 import build_config_bird2_context, render_config_bird2_set, render_config_bird2_template
from .coredns import render_corefile, render_dns_zone, zone_file_name
from .scripts import (
    render_apply_all_wg_script,
    render_bird_apply_script,
    render_bird_start_script,
    render_loopback_script,
    render_wireguard_apply_script,
    render_wireguard_start_script,
)
from .wireguard import render_wireguard


def render_desired_state(state: DesiredState) -> list[RenderedFile]:
    """渲染单个节点 deployment 所需的全部文件。

    输出文件通常包括：

    - BIRD 配置文件集合
    - WireGuard 接口配置与应用脚本
    - 路由器启动脚本
    - 可选的 CoreDNS Corefile

    容器层不渲染任何文件：编排（网络/端口/挂载/依赖）以 `state.runtime`
    的结构化数据直达 agent 的 Docker Engine API 后端；router 镜像的
    Dockerfile 同样由 agent 按 `runtime.router_dockerfile` 在内存生成并
    经 Engine API 构建（`dn42_runtime.render_router_dockerfile`）。

    调用方可以把返回值直接交给 runtime 层落盘，也可以在测试里只检查某个路径的内容。
    """

    files = [
        RenderedFile("scripts/bird/apply-bird.sh", render_bird_apply_script()),
        RenderedFile("scripts/bird/start-bird-router.sh", render_bird_start_script(state)),
        RenderedFile("scripts/wg/apply-dn42-lo.sh", render_loopback_script(state)),
        RenderedFile("scripts/wg/apply-all-wg.sh", render_apply_all_wg_script(state)),
        RenderedFile("scripts/wg/start-wg-gateway.sh", render_wireguard_start_script()),
    ]

    bird_context = build_config_bird2_context(state)
    files.extend(
        RenderedFile(f"bird/{rendered.path}", rendered.content)
        for rendered in render_config_bird2_set(bird_context)
    )

    for interface in sorted(state.interfaces, key=lambda item: item.name):
        if interface.kind == InterfaceKind.WIREGUARD:
            files.append(
                RenderedFile(f"wireguard/{interface.name}.conf", render_wireguard(interface))
            )
            files.append(
                RenderedFile(
                    f"scripts/wg/apply-{interface.name}.sh",
                    render_wireguard_apply_script(interface),
                )
            )

    if state.dns and state.dns.enabled:
        files.append(RenderedFile("coredns/Corefile", render_corefile(state)))
        for zone in state.dns.zones:
            if zone.records:
                files.append(
                    RenderedFile(
                        f"coredns/zones/db.{zone_file_name(zone)}",
                        render_dns_zone(zone, serial=state.generation),
                    )
                )

    return sorted(files, key=lambda item: item.path)


def render_bird_conf(state: DesiredState) -> str:
    """仅渲染聚合后的 `bird.conf` 主配置。

    这个 helper 适合模板单测或快速查看最终主配置，
    不负责返回整套 deployment 文件列表。
    """

    return render_config_bird2_template("bird.conf", build_config_bird2_context(state))


__all__ = ["render_bird_conf", "render_desired_state"]
