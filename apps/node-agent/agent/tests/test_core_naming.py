from __future__ import annotations

"""节点 agent 内部使用的命名 helper 的单元测试。

agent 会以这些名字 ``label`` / 定位 / 删除容器，控制面渲染也必须遵守
同样规则，本文件锁定三项不变量：

* ``node_project_name(state)``：默认产出 ``dn42-{normalized
  node_id}``（例如 ``dn42-edge1``）。
* 若 ``state.runtime.project_name`` 显式设置，返回该值（例如
  ``lab-hkg``），覆写默认拼接。
* ``service_container_name(project, service)`` 末尾追加 docker compose 默认
  的 ``-1`` 实例后缀，与现场 docker 生成的容器名字 1∶1 对齐。
"""

from dn42_schemas.testing import build_hkg1_example_state

from agent.core.naming import node_project_name, service_container_name


def test_node_project_name_normalizes_node_id() -> None:
    state = build_hkg1_example_state()

    assert node_project_name(state) == "dn42-edge1"


def test_node_project_name_prefers_explicit_runtime_value() -> None:
    state = build_hkg1_example_state()
    state = state.__class__.model_validate(
        {**state.model_dump(mode="json"), "runtime": {**state.runtime.model_dump(mode="json"), "project_name": "lab-hkg"}}
    )

    assert node_project_name(state) == "lab-hkg"


def test_service_container_name_appends_index_suffix() -> None:
    assert service_container_name("dn42-edge1", "dn42-bird-router") == "dn42-edge1-dn42-bird-router-1"
