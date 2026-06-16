from __future__ import annotations

"""容器定义（`agent.planner.definition`）的不变量测试。

* 身份哈希的输入是**最终 Engine API payload**，不是 schema 序列化——
  generation 递增、schema 形状重构都不影响身份；
* 只有真实定义变化（命令/端口/underlay/镜像配方……）才改变哈希；
* Dockerfile 内容以 sha256 进入 payload：模板参数变化只影响本地构建服务；
* ``diff_payload_keys`` 给出字段级差异，供 recreate reason 使用；
* label 集合由定义派生，``dn42.config_hash`` 与定义哈希一致。
"""

from pathlib import Path

from dn42_common import LABEL_COMPONENT, LABEL_CONFIG_HASH, LABEL_MANAGED_VALUE, LABEL_MANAGED, LABEL_NODE_ID
from dn42_schemas.testing import build_hkg1_example_state

from agent.planner.definition import (
    build_node_definitions,
    diff_payload_keys,
    payload_hash,
)

_DIR = Path("rendered")


def test_hash_stable_across_generation_bumps() -> None:
    """generation 递增不得改变任何服务的身份哈希——最小扰动的根基。"""

    state = build_hkg1_example_state()
    bumped = state.model_copy(update={"generation": state.generation + 1})
    before = {n: d.config_hash for n, d in build_node_definitions(state, _DIR).items()}
    after = {n: d.config_hash for n, d in build_node_definitions(bumped, _DIR).items()}
    assert before == after


def test_hash_changes_when_service_definition_changes() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    service = next(s for s in data["runtime"]["services"] if s["name"] == "dn42-rpki-cache")
    service["environment"] = {"X": "1"}
    changed = state.__class__.model_validate(data)

    before = build_node_definitions(state, _DIR)["dn42-rpki-cache"]
    after = build_node_definitions(changed, _DIR)["dn42-rpki-cache"]

    assert before.config_hash != after.config_hash
    assert diff_payload_keys(before.payload, after.payload) == ["environment"]


def test_hash_changes_when_underlay_changes() -> None:
    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["underlay"]["subnet"] = "10.254.99.0/24"
    data["runtime"]["underlay"]["gateway"] = "10.254.99.1"
    rpki = next(s for s in data["runtime"]["services"] if s["name"] == "dn42-rpki-cache")
    netns_ip = "10.254.99.3"
    data["runtime"]["rpki"]["listen_host"] = netns_ip
    changed = state.__class__.model_validate(data)

    before = build_node_definitions(state, _DIR)["dn42-router-netns"]
    after = build_node_definitions(changed, _DIR)["dn42-router-netns"]

    # underlay 变化体现在 network.attach 的 ipv4 上 → 哈希必须变。
    assert before.config_hash != after.config_hash
    assert "network" in diff_payload_keys(before.payload, after.payload)


def test_dockerfile_params_only_affect_built_services() -> None:
    """Dockerfile 模板参数只影响需要本地构建的服务的身份。"""

    state = build_hkg1_example_state()
    data = state.model_dump(mode="json")
    data["runtime"]["router_dockerfile"]["debian_mirror"] = "mirrors.example.com"
    changed = state.__class__.model_validate(data)

    before = build_node_definitions(state, _DIR)
    after = build_node_definitions(changed, _DIR)

    for name, definition in before.items():
        if definition.dockerfile is not None:
            assert definition.config_hash != after[name].config_hash, name
            assert diff_payload_keys(definition.payload, after[name].payload) == ["image"]
        else:
            assert definition.config_hash == after[name].config_hash, name


def test_built_definition_carries_dockerfile_and_its_digest() -> None:
    state = build_hkg1_example_state()
    netns = build_node_definitions(state, _DIR)["dn42-router-netns"]

    assert netns.dockerfile is not None
    assert "FROM debian:13-slim" in netns.dockerfile
    build = netns.payload["image"]["build"]
    assert build["target"] == "netns"
    assert len(build["dockerfile_sha256"]) == 64


def test_hash_is_canonical_payload_digest() -> None:
    state = build_hkg1_example_state()
    definition = build_node_definitions(state, _DIR)["dn42-rpki-cache"]
    assert definition.config_hash == payload_hash(definition.payload)
    assert len(definition.config_hash) == 16


def test_labels_derive_from_definition() -> None:
    state = build_hkg1_example_state()
    definition = build_node_definitions(state, _DIR)["dn42-bird-router"]
    labels = definition.labels

    assert labels[LABEL_MANAGED] == LABEL_MANAGED_VALUE
    assert labels[LABEL_NODE_ID] == state.node.node_id
    assert labels[LABEL_COMPONENT] == "bird-router"
    assert labels[LABEL_CONFIG_HASH] == definition.config_hash


def test_network_mode_service_resolves_to_container_reference() -> None:
    state = build_hkg1_example_state()
    definitions = build_node_definitions(state, _DIR)
    bird = definitions["dn42-bird-router"]
    netns = definitions["dn42-router-netns"]

    assert bird.payload["network"] == {"mode": f"container:{netns.container_name}"}
