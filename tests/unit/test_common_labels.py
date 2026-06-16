from __future__ import annotations

""":mod:`dn42_common` 对外暴露的 docker label 常量与生成函数的单元测试。

控制面 / agent / planner 都靠这些 label 识别 "dn42-control-backend
接管的资源"，因此该套用例锁定以下不变量：

* ``network_labels()`` 仅输出 ``dn42.managed=true`` 这一个唯一标记。
* ``container_labels(node_id, component, config_hash)`` 输出 **四项** label：
  ``dn42.managed`` / ``dn42.node_id`` / ``dn42.config_hash`` /
  ``dn42.component``。该"四项集合"被运行时 reconcile 依赖。
  哈希的**计算**不在本包——它来自 agent 决策层的容器定义
  （`agent.planner.definition`，见 agent 测试），label 只是存储位置。
* ``node_id_filter(node_id)`` 返回 docker SDK 可直接使用的
  ``["dn42.node_id=..."]`` 过滤列表。
"""

from dn42_common import (
    LABEL_COMPONENT,
    LABEL_CONFIG_HASH,
    LABEL_MANAGED,
    LABEL_MANAGED_VALUE,
    LABEL_NODE_ID,
    container_labels,
    network_labels,
    node_id_filter,
)


class TestNetworkLabels:
    def test_only_managed_marker(self) -> None:
        labels = network_labels()
        assert labels == {LABEL_MANAGED: LABEL_MANAGED_VALUE}


class TestContainerLabels:
    def test_includes_all_four_keys(self) -> None:
        labels = container_labels("edge1", "bird-router", "cafe0123cafe0123")
        assert labels == {
            LABEL_MANAGED: LABEL_MANAGED_VALUE,
            LABEL_NODE_ID: "edge1",
            LABEL_CONFIG_HASH: "cafe0123cafe0123",
            LABEL_COMPONENT: "bird-router",
        }


class TestNodeIdFilter:
    def test_returns_label_filter_pair(self) -> None:
        assert node_id_filter("edge1") == ["dn42.node_id=edge1"]
