from __future__ import annotations

"""``_derive_bird_socket_path``：从 bird 服务的 /run/bird 挂载推导宿主侧 socket 路径。

保证 agent 连接路径与 ``definition.resolve_volume_source`` 解析出的容器 bind 落点一致；
bird 服务缺该挂载（旧快照未升级）时返回 None，调用方据此跳过本轮采集。
"""

from pathlib import Path

from dn42_schemas import ServiceRole
from dn42_schemas.testing import build_hkg1_example_state

from agent.planner.definition import resolve_volume_source
from agent.routing import _BIRD_RUN_TARGET, _derive_bird_socket_path


def _bird_service(state):
    return next(s for s in state.runtime.services if s.role == ServiceRole.BIRD_ROUTER)


def test_derive_matches_container_bind_resolution() -> None:
    state = build_hkg1_example_state()
    rendered_dir = Path("/var/lib/dn42-control/nodes/hkg1/rendered")

    derived = _derive_bird_socket_path(state, rendered_dir)

    # 与容器 bind 同一套 source 解析 + bird.ctl 文件名。
    mount = next(m for m in _bird_service(state).volumes if m.target == _BIRD_RUN_TARGET)
    expected = str(Path(resolve_volume_source(rendered_dir, mount)) / "bird.ctl")
    assert derived == expected
    assert derived.endswith("bird.ctl")


def test_derive_returns_none_without_bird_service() -> None:
    # 没有 bird-router 服务（极简 / 异常状态）⇒ None；调用方据此跳过本轮采集。
    # 经 model_copy 去掉 bird 服务并 object.__setattr__ 注入，绕过 DesiredState 重校验
    # （后者会无条件回注 bird 的 /run/bird 挂载，且要求 bird 服务存在）。
    state = build_hkg1_example_state()
    services = [s for s in state.runtime.services if s.role != ServiceRole.BIRD_ROUTER]
    object.__setattr__(state, "runtime", state.runtime.model_copy(update={"services": services}))

    assert _derive_bird_socket_path(state, Path("/x/rendered")) is None
