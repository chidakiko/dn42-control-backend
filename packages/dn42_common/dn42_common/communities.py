from __future__ import annotations

"""DN42 标准 BGP community 编号空间。

DN42 使用 `(64511, *)` 作为全网约定 community，本模块只定义两类：

* `(64511, 41..57)` 起源 region 编号——枚举为 `Dn42OriginRegionCommunity`。
* `(64511, 1000 + ISO-3166-1-numeric)` 起源国家编号。

本模块不负责 BIRD 渲染，只负责给出讯号 / 范围 / 映射表。
"""

from enum import IntEnum


class Dn42OriginRegionCommunity(IntEnum):
    """DN42 standard origin region community 枚举（17 个化名值）41..57）。

    与 BIRD `large` community 衔接时表示为 `(OWNAS, REGION_TYPE, value)`，
    其中 `value` 即本枚举的整数值。定义参考 dn42 wiki BGP communities 页。
    """

    EUROPE = 41
    NORTH_AMERICA_EAST = 42
    NORTH_AMERICA_CENTRAL = 43
    NORTH_AMERICA_WEST = 44
    CENTRAL_AMERICA = 45
    SOUTH_AMERICA_EAST = 46
    SOUTH_AMERICA_WEST = 47
    AFRICA_NORTH = 48
    AFRICA_SOUTH = 49
    ASIA_SOUTH = 50
    ASIA_SOUTHEAST = 51
    ASIA_EAST = 52
    PACIFIC_OCEANIA = 53
    ANTARCTICA = 54
    ASIA_NORTH = 55
    ASIA_WEST = 56
    CENTRAL_ASIA = 57


DN42_STANDARD_ORIGIN_REGION_COMMUNITIES = {
    Dn42OriginRegionCommunity.EUROPE: "Europe",
    Dn42OriginRegionCommunity.NORTH_AMERICA_EAST: "North America-E",
    Dn42OriginRegionCommunity.NORTH_AMERICA_CENTRAL: "North America-C",
    Dn42OriginRegionCommunity.NORTH_AMERICA_WEST: "North America-W",
    Dn42OriginRegionCommunity.CENTRAL_AMERICA: "Central America",
    Dn42OriginRegionCommunity.SOUTH_AMERICA_EAST: "South America-E",
    Dn42OriginRegionCommunity.SOUTH_AMERICA_WEST: "South America-W",
    Dn42OriginRegionCommunity.AFRICA_NORTH: "Africa-N",
    Dn42OriginRegionCommunity.AFRICA_SOUTH: "Africa-S",
    Dn42OriginRegionCommunity.ASIA_SOUTH: "Asia-S",
    Dn42OriginRegionCommunity.ASIA_SOUTHEAST: "Asia-SE",
    Dn42OriginRegionCommunity.ASIA_EAST: "Asia-E",
    Dn42OriginRegionCommunity.PACIFIC_OCEANIA: "Pacific and Oceania",
    Dn42OriginRegionCommunity.ANTARCTICA: "Antarctica",
    Dn42OriginRegionCommunity.ASIA_NORTH: "Asia-N",
    Dn42OriginRegionCommunity.ASIA_WEST: "Asia-W",
    Dn42OriginRegionCommunity.CENTRAL_ASIA: "Central Asia",
}

DN42_COUNTRY_ORIGIN_OFFSET = 1000
DN42_COUNTRY_ORIGIN_COMMUNITY_MIN = 1000
DN42_COUNTRY_ORIGIN_COMMUNITY_MAX = 1999


def dn42_country_origin_community(iso3166_numeric: int) -> int:
    """把 ISO-3166-1 numeric 国家码映射为 DN42 起源国家 community 值。

    返回 `code + DN42_COUNTRY_ORIGIN_OFFSET`（中间没有跳号）。超出 `0..999`
    范围会报错，避免被危险的“似是越界但息事宁人”隐式转换。
    """

    if iso3166_numeric < 0 or iso3166_numeric > 999:
        raise ValueError("ISO-3166-1 numeric code must be between 0 and 999")
    return DN42_COUNTRY_ORIGIN_OFFSET + iso3166_numeric


def is_valid_dn42_country_origin_community(value: int) -> bool:
    """`value` 是否落在起源国家 community 的合法区间。非抛出谓词。"""

    return DN42_COUNTRY_ORIGIN_COMMUNITY_MIN <= value <= DN42_COUNTRY_ORIGIN_COMMUNITY_MAX