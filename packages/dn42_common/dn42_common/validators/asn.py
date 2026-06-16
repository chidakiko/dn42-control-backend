from __future__ import annotations

"""ASN 校验与私有 ASN 判定。

私有 ASN 范围参考 RFC 6996 / RFC 7300：
- 16 位私有：64512 – 65534
- 32 位私有：4_200_000_000 – 4_294_967_294

DN42 默认在 32 位段内分配（4242420000+）。
"""


PRIVATE_ASN_16BIT_RANGE: tuple[int, int] = (64512, 65534)
PRIVATE_ASN_32BIT_RANGE: tuple[int, int] = (4_200_000_000, 4_294_967_294)


def validate_asn(value: int) -> int:
    """校验 ASN 是否在 [1, 2^32-1] 范围内。"""

    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"asn must be int, got {type(value).__name__}")
    if value < 1 or value > 4_294_967_295:
        raise ValueError(f"asn {value} out of range [1, 4294967295]")
    return value


def is_private_asn(value: int) -> bool:
    """是否为 RFC 6996 私有 ASN（dn42 默认使用 32 位私有段）。"""

    if PRIVATE_ASN_16BIT_RANGE[0] <= value <= PRIVATE_ASN_16BIT_RANGE[1]:
        return True
    if PRIVATE_ASN_32BIT_RANGE[0] <= value <= PRIVATE_ASN_32BIT_RANGE[1]:
        return True
    return False


__all__ = [
    "PRIVATE_ASN_16BIT_RANGE",
    "PRIVATE_ASN_32BIT_RANGE",
    "is_private_asn",
    "validate_asn",
]
