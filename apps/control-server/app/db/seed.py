from __future__ import annotations

"""控制服务器启动时的数据 seed。

只做一件事：如果 ``nodes`` 表为空，把 ``build_hkg1_example_state()`` 拆解成
``Node + base_template + WgInterface + BgpSession + AgentToken`` 写进 DB，然后
调用 materializer 产生 ``generation=1`` 的 snapshot。

这样：
- 全新数据库 / 内存 SQLite → 启动后立刻有 demo 节点可联调；
- ``DesiredState`` 的"事实来源"完全是 DB，agent 拉到的 snapshot 是
  materializer 从子表组装的，与例子模型一字不差。
- 已有数据库 → 不动任何东西。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dn42_schemas import DesiredState
from dn42_schemas.testing import build_hkg1_example_state

from ..core.config import ControlServerConfig
from .models import Node
from .provision import provision_node_from_state


# 仅用于让本地联调时 wg 配置看起来"像样"；下一轮真实控制面会把 key 写入业务表。
_DEMO_WIREGUARD_KEYS: dict[str, dict[str, str]] = {
    "as4242420001": {
        "private": "s2ljcc2rBbcmSbpSlQO3xZK20RqSxPFOcYM39Ge678M=",
        "public": "ZBzpHBTCXDYmjXzsiyZ+eWYClAQX10pCgr+Lr+oJlbc=",
    },
    "igp-edge2": {
        "private": "NGC5eyocjJudNHf9EoaCCIRH50NZtaMHfyZT1CqpPAs=",
        "public": "dqU1WhGGCztFvUaSvV3PDNPRXtjm87spbQcQITzlaj0=",
    },
}


async def seed_initial_data(
    session: AsyncSession, config: ControlServerConfig
) -> None:
    """按需播种 demo 节点。

    默认（``config.seed_bootstrap_node=False``）下完全不写入任何数据——启动即空库，
    节点应由导入 / provision 流程写入。仅当显式开启 ``seed_bootstrap_node`` 且库里
    还没有任何节点时，才把内置 HKG1 demo 拆解落库，方便本地联调 / 测试。
    """

    if not config.seed_bootstrap_node:
        return

    has_node = (await session.execute(select(Node.node_id))).first() is not None
    if has_node:
        return

    state = _patch_demo_keys(build_hkg1_example_state())
    await provision_node_from_state(
        session,
        state,
        agent_token=config.bootstrap_agent_token,
        reason="bootstrap seed",
    )


def _patch_demo_keys(state: DesiredState) -> DesiredState:
    patched = []
    for interface in state.interfaces:
        keys = _DEMO_WIREGUARD_KEYS.get(interface.name)
        if not keys:
            patched.append(interface)
            continue
        peer = interface.wireguard_peer
        patched.append(
            interface.model_copy(
                update={
                    "private_key_ref": keys["private"],
                    "wireguard_peer": (
                        peer.model_copy(update={"public_key": keys["public"]})
                        if peer is not None
                        else None
                    ),
                }
            )
        )
    return state.model_copy(update={"interfaces": patched})


__all__ = ["seed_initial_data"]
