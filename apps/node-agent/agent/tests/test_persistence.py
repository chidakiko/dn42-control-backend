from __future__ import annotations

"""节点 agent 本地身份与 desired-state 缓存的持久化单元测试。

agent 启动时需要 “恢复上一次身份 ”：节点 ID、agent token、已应用的
``applied_generation``。本文件锁定以下行为：

* ``LocalAgentIdentity`` 走 ``save_identity`` -> 磁盘 -> ``load_identity``
  的完整 round-trip，反序列化后与原对象相等；
* ``load_identity`` 在文件不存在时返回“空身份” (token / node_id 为
  None) 而非报错，供冷启动场景使用；
* ``save_cached_desired_state`` / ``load_cached_desired_state`` 会作为 agent
  离线 reconcile 时的 fallback 可用；
* ``load_desired_state_from_file`` 会走 schema 验证，坏文件报
  ``DesiredStateError``（而不是静默返回部分解析结果）。
"""

import json
from pathlib import Path

import pytest

from agent.core.identity import LocalAgentIdentity, load_identity, save_identity
from agent.desired_state.cache import load_cached_desired_state, save_cached_desired_state
from agent.desired_state.loader import load_desired_state_from_file
from agent.core.errors import DesiredStateError
from dn42_schemas.testing import build_hkg1_example_state


def test_save_and_load_identity_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "identity.json"
    identity = LocalAgentIdentity(
        node_id="edge1", agent_id="edge1-agent", agent_token="t", applied_generation=42
    )

    save_identity(identity, path)

    assert path.exists()
    loaded = load_identity(path)
    assert loaded == identity


def test_load_identity_returns_empty_when_missing(tmp_path: Path) -> None:
    loaded = load_identity(tmp_path / "missing.json")

    assert loaded.node_id is None
    assert loaded.agent_token is None


def test_save_and_load_cached_desired_state_roundtrip(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    path = tmp_path / "desired-state.json"

    save_cached_desired_state(state, path)
    loaded = load_cached_desired_state(path)

    assert loaded is not None
    assert loaded.node.node_id == state.node.node_id


def test_load_desired_state_from_file_validates(tmp_path: Path) -> None:
    state = build_hkg1_example_state()
    path = tmp_path / "ds.json"
    path.write_text(json.dumps(state.model_dump(mode="json")), encoding="utf-8")

    loaded = load_desired_state_from_file(path)

    assert loaded.generation == state.generation


def test_load_desired_state_rejects_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "ds.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(DesiredStateError):
        load_desired_state_from_file(path)
