from __future__ import annotations

""":mod:`dn42_common` 中跨仓复用的 docker compose 命名工具的单元测试。

该文件锁定以下行为，以免控制面与节点 agent 两端产生不一致的
容器名者破坏 reconcile：

* ``normalize_identifier``：全小写化、压缩连续不安全字符为单个
  ``-``、去除首尾 ``-``、保留 ``_`` 与 ASCII 字母数字。
* ``node_project_name``：override 非空时原样返回；空串与 ``None``
  被视为未提供，退回到 ``dn42-{normalized node_id}``。
* ``service_container_name``：拼接为 ``{project}-{service}-1``，与 docker
  compose 默认命名规范一致，不论传入是不是合法 docker 名。
"""

import pytest

from dn42_common import (
    node_project_name,
    normalize_identifier,
    service_container_name,
)


class TestNormalizeIdentifier:
    def test_lowercases_and_strips_unsafe_chars(self) -> None:
        assert normalize_identifier("Node1.Edge!") == "node1-edge"

    def test_collapses_runs_of_unsafe_chars(self) -> None:
        assert normalize_identifier("a..b___c--d") == "a-b___c--d"
        assert normalize_identifier("a   b") == "a-b"

    def test_strips_leading_and_trailing_separators(self) -> None:
        assert normalize_identifier("---hkg1---") == "hkg1"
        assert normalize_identifier("__hkg1__") == "__hkg1__"  # 下划线不属于分隔符

    def test_preserves_alphanumeric_and_safe_punctuation(self) -> None:
        assert normalize_identifier("hkg1_edge-001") == "hkg1_edge-001"


class TestComposeProjectName:
    def test_uses_override_when_present(self) -> None:
        assert node_project_name("hkg1", override="custom") == "custom"

    def test_falls_back_to_normalized_node_id(self) -> None:
        assert node_project_name("Node1.Edge") == "dn42-node1-edge"

    def test_treats_empty_override_as_absent(self) -> None:
        assert node_project_name("hkg1", override="") == "dn42-hkg1"
        assert node_project_name("hkg1", override=None) == "dn42-hkg1"


class TestServiceContainerName:
    @pytest.mark.parametrize(
        "project,service,expected",
        [
            ("dn42-hkg1", "dn42-router-netns", "dn42-hkg1-dn42-router-netns-1"),
            ("dn42-hkg1", "Bird Router", "dn42-hkg1-bird-router-1"),
        ],
    )
    def test_combines_project_and_service(self, project: str, service: str, expected: str) -> None:
        assert service_container_name(project, service) == expected
