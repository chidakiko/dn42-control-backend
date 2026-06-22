from __future__ import annotations

""":class:`DesiredState` 的 canonical JSON 序列化、SHA-256 摘要与 IO 加载测试。

DesiredState 会被签名（Ed25519）与哈希，任何不稳定的序列化都会造成
签名 “时而有效、时而失效”。本文件锐意锁定以下不变量：

* ``canonical_json`` 输出是紧凑的（不含 ``", "`` / ``": "`` 空格），
  可被反序列化后与原对象相等，且多次调用完全一致。
* ``canonical_sha256`` 与 “手工对 canonical_json 取 SHA-256”严格一致；
  任何字段变动（如 ``generation`` 递增 1）都会产生不同 hash。
* ``load_desired_state`` 支持 JSON / YAML（yaml 可选依赖使用
  ``importorskip``）与未知后缀的退退 fallback；缺文件报
  ``FileNotFoundError``，坏 JSON 报 ``ValueError``。
"""

import json
from pathlib import Path

import pytest

from dn42_schemas import DesiredState, load_desired_state
from dn42_schemas.testing import build_hkg1_example_state


class TestCanonicalSerialization:
    def test_canonical_json_is_sorted_and_compact(self) -> None:
        state = build_hkg1_example_state()
        canonical = state.canonical_json()
        # 紧凑 separators：没有 ", " 或 ": " 空格。
        assert ", " not in canonical
        assert '": ' not in canonical
        # 可被重新解析为等价对象。
        reparsed = DesiredState.model_validate(json.loads(canonical))
        assert reparsed == state

    def test_canonical_json_is_stable_across_calls(self) -> None:
        state = build_hkg1_example_state()
        assert state.canonical_json() == state.canonical_json()

    def test_canonical_sha256_matches_manual_hash(self) -> None:
        import hashlib

        state = build_hkg1_example_state()
        expected = hashlib.sha256(state.canonical_json().encode("utf-8")).hexdigest()
        assert state.canonical_sha256() == expected

    def test_canonical_sha256_changes_with_content(self) -> None:
        state = build_hkg1_example_state()
        data = state.model_dump(mode="json")
        data["generation"] = state.generation + 1
        bumped = DesiredState.model_validate(data)
        assert bumped.canonical_sha256() != state.canonical_sha256()


class TestLoadDesiredState:
    def test_load_json(self, tmp_path: Path) -> None:
        state = build_hkg1_example_state()
        path = tmp_path / "state.json"
        path.write_text(state.canonical_json(), encoding="utf-8")
        loaded = load_desired_state(path)
        assert loaded == state

    def test_load_yaml(self, tmp_path: Path) -> None:
        yaml = pytest.importorskip("yaml")
        state = build_hkg1_example_state()
        path = tmp_path / "state.yaml"
        path.write_text(yaml.safe_dump(state.model_dump(mode="json")), encoding="utf-8")
        loaded = load_desired_state(path)
        assert loaded == state

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_desired_state(tmp_path / "nope.json")

    def test_invalid_json_raises_value_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            load_desired_state(path)

    def test_unknown_extension_falls_back(self, tmp_path: Path) -> None:
        state = build_hkg1_example_state()
        path = tmp_path / "state.txt"
        path.write_text(state.canonical_json(), encoding="utf-8")
        loaded = load_desired_state(path)
        assert loaded == state
