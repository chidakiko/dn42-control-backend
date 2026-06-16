from __future__ import annotations

"""DN42 标准“起源区域 / 起源国家” BGP community 表的单元测试。

覆盖 :mod:`dn42_common` 中与路由策略相关的两类社区助手：

* ``Dn42OriginRegionCommunity`` 枚举与 ``DN42_STANDARD_ORIGIN_REGION_COMMUNITIES``
  映射表，验证 Asia-East / Asia-West 等区域号与名称与 DN42 官方
  文档一致（避免后续重构静默修改社区号）。
* ``dn42_country_origin_community`` / ``is_valid_dn42_country_origin_community``
  实现的 ``1000 + ISO 3166 数字码`` 偏移规则，验证同样包括
  越界拒绝（``< 1000`` 或 ``> 1999``）。
"""

import pytest

from dn42_common import (
    DN42_STANDARD_ORIGIN_REGION_COMMUNITIES,
    Dn42OriginRegionCommunity,
    dn42_country_origin_community,
    is_valid_dn42_country_origin_community,
)


def test_dn42_standard_origin_region_communities_include_current_regions() -> None:
    assert Dn42OriginRegionCommunity.ASIA_EAST == 52
    assert Dn42OriginRegionCommunity.ASIA_WEST == 56
    assert DN42_STANDARD_ORIGIN_REGION_COMMUNITIES[Dn42OriginRegionCommunity.ASIA_EAST] == "Asia-E"
    assert DN42_STANDARD_ORIGIN_REGION_COMMUNITIES[Dn42OriginRegionCommunity.ASIA_WEST] == "Asia-W"


def test_dn42_country_origin_community_uses_iso_numeric_offset() -> None:
    assert dn42_country_origin_community(344) == 1344
    assert dn42_country_origin_community(392) == 1392
    assert is_valid_dn42_country_origin_community(1344) is True
    assert is_valid_dn42_country_origin_community(999) is False


def test_dn42_country_origin_community_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError):
        dn42_country_origin_community(1000)