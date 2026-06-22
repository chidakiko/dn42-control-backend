from __future__ import annotations

"""从 ``DesiredState`` 到“差异计划”的端到端渲染流水线集成测试。

agent 在落盘之前需要把希望状态转为一组对应磁盘文件的
``RenderedFile``，再交给 ``build_file_plan`` 判定创建 / 更新 / 保持。
本文件覆盖该流水线的下列不变量：

* ``render_desired_state`` 以 hkg1 sample state 为输入，产出必须包含
  bird 配置、wireguard 配置、起动脚本与 CoreDNS Corefile 等预期路径；
  **绝不**再渲染 docker-compose.yml 或 router Dockerfile——容器编排与
  镜像构建都由结构化 runtime 数据直达 Docker Engine API。
* ``build_file_plan`` 在空目录上报 ``create``，同文本报 ``noop``，在
  未渲染文本被人工修改后报 ``update``。
* runtime ``project_name`` 与 RPKI ``ipv4_address`` 覆盖能同步作用到
  结构化 runtime 解析与 BIRD 上下文。
"""

from pathlib import Path

from dn42_common import node_project_name
from dn42_runtime import build_file_plan, write_rendered_files
from dn42_schemas import resolve_service_ipv4
from dn42_schemas.testing import build_hkg1_example_state
from dn42_templates import build_config_bird2_context
from dn42_templates import render_desired_state


def test_desired_state_renders_expected_agent_files() -> None:
    state = build_hkg1_example_state()
    rendered = render_desired_state(state)
    paths = {file.path for file in rendered}

    # 容器编排与镜像构建不再渲染文件——这是去 compose/Dockerfile 化后的回归锁。
    assert "docker-compose.yml" not in paths
    assert "docker/router/Dockerfile" not in paths
    assert "bird/bird.conf" in paths
    assert "bird/dn42_peers.conf" in paths
    assert "bird/community_filters.conf" in paths
    assert "bird/custom_filters.conf" in paths
    assert "bird/rpki.conf" in paths
    assert "wireguard/as4242420001.conf" in paths
    assert "wireguard/igp-edge2.conf" in paths
    assert "scripts/bird/apply-bird.sh" in paths
    assert "scripts/bird/start-bird-router.sh" in paths
    assert "scripts/wg/apply-as4242420001.sh" in paths
    assert "scripts/wg/apply-igp-edge2.sh" in paths
    assert "scripts/wg/start-wg-gateway.sh" in paths
    assert "coredns/Corefile" in paths

    bird_conf = next(file.content for file in rendered if file.path == "bird/bird.conf")
    assert 'include "/etc/bird/dn42_peers.conf";' in bird_conf
    assert 'include "/etc/bird/rpki.conf";' in bird_conf

    custom_filters = next(file.content for file in rendered if file.path == "bird/custom_filters.conf")
    assert "define NODEID = 62;" in custom_filters
    assert "define LC_ORIGIN_NODEID = 100;" in custom_filters
    assert "define LC_ORIGIN_REGION = 101;" in custom_filters
    assert "define LC_POLICY = 102;" in custom_filters

    dn42_peers = next(file.content for file in rendered if file.path == "bird/dn42_peers.conf")
    assert "protocol bgp demopeer_4242420001_ex01_v4 from dnpeers" in dn42_peers
    assert "neighbor 172.20.0.105 as 4242420001;" in dn42_peers

    ibgp = next(file.content for file in rendered if file.path == "bird/ibgp.conf")
    assert "protocol bgp ibgp_edge2" in ibgp


def test_file_plan_tracks_create_noop_and_update(tmp_path: Path) -> None:
    rendered = render_desired_state(build_hkg1_example_state())

    create_plan = build_file_plan(rendered, tmp_path)
    assert create_plan.summary.create == len(rendered)
    assert create_plan.summary.noop == 0

    write_rendered_files(rendered, tmp_path)
    assert (tmp_path / "bird" / "bird.conf").exists()
    noop_plan = build_file_plan(rendered, tmp_path)
    assert noop_plan.summary.noop == len(rendered)
    assert noop_plan.summary.create == 0

    drift_path = tmp_path / "bird" / "bird.conf"
    drift_path.write_text(drift_path.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
    update_plan = build_file_plan(rendered, tmp_path)
    assert update_plan.summary.update == 1


def test_explicit_rpki_container_ip_updates_runtime_and_bird_context() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    rpki_service = next(service for service in data["runtime"]["services"] if service["name"] == "dn42-rpki-cache")
    rpki_service["ipv4_address"] = "10.254.42.44"

    validated = state.__class__.model_validate(data)
    rpki = next(service for service in validated.runtime.services if service.name == "dn42-rpki-cache")
    bird_context = build_config_bird2_context(validated)

    assert resolve_service_ipv4(validated.runtime, rpki) == "10.254.42.44"
    assert bird_context["rpki_ip"] == "10.254.42.44"


def test_runtime_project_name_overrides_node_project_name() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["project_name"] = "lab-hkg1"

    validated = state.__class__.model_validate(data)

    assert (
        node_project_name(validated.node.node_id, override=validated.runtime.project_name)
        == "lab-hkg1"
    )
