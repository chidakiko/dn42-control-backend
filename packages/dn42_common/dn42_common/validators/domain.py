from __future__ import annotations

"""DNS 域名 / 主机名格式校验。

参考 RFC 1035 / RFC 1123 / RFC 5891：

- 总长度 ≤ 253 字符（不含尾部 `.`）
- 每个 label：1-63 字符，仅允许 `[A-Za-z0-9-]`，不能以 `-` 开头或结尾
- 默认接受根域 `.` 结尾的写法
- 默认拒绝纯数字顶级 label（避免与 IPv4 混淆）

本模块不做 IDN punycode 解析，调用方在传入前应自行 `idna.encode` 化。
"""

import re


_DOMAIN_MAX_LENGTH = 253
_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_LABEL_PATTERN_UNDERSCORE = re.compile(
    r"^[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?$"
)


def validate_domain_name(
    value: str,
    *,
    allow_trailing_dot: bool = True,
    allow_underscore: bool = False,
    allow_slash: bool = False,
    require_multi_label: bool = True,
) -> str:
    """校验 DNS 域名格式。

    参数：

    - `allow_trailing_dot`：是否允许 FQDN 的根域 `.`，默认允许并在返回值
      中保留原写法。
    - `allow_underscore`：是否允许下划线（用于 SRV/_acme-challenge 等记录
      的标签），默认 False。
    - `allow_slash`：是否允许标签中含 `/`（用于 RFC 2317 无类反向委派 zone，
      如 `0/26.0.20.172.in-addr.arpa`），默认 False。
    - `require_multi_label`：是否要求至少两段，默认 True；设为 False 后可以
      接受 `localhost`、`router` 等单段名。
    """

    if not isinstance(value, str) or not value:
        raise ValueError("domain must be a non-empty string")

    candidate = value
    had_trailing_dot = candidate.endswith(".")
    if had_trailing_dot:
        if not allow_trailing_dot:
            raise ValueError(f"{value!r} must not end with '.'")
        candidate = candidate[:-1]

    if not candidate:
        raise ValueError(f"{value!r} has no labels")
    if len(candidate) > _DOMAIN_MAX_LENGTH:
        raise ValueError(
            f"domain length {len(candidate)} exceeds RFC 1035 limit ({_DOMAIN_MAX_LENGTH})"
        )

    labels = candidate.split(".")
    if require_multi_label and len(labels) < 2:
        raise ValueError(f"{value!r} must contain at least two labels")

    if allow_slash:
        extra = "_" if allow_underscore else ""
        pattern = re.compile(
            rf"^[A-Za-z0-9{extra}](?:[A-Za-z0-9{extra}/-]{{0,61}}[A-Za-z0-9{extra}])?$"
        )
    else:
        pattern = _LABEL_PATTERN_UNDERSCORE if allow_underscore else _LABEL_PATTERN
    for label in labels:
        if not label:
            raise ValueError(f"{value!r} has an empty label")
        if not pattern.fullmatch(label):
            raise ValueError(f"{value!r} contains invalid label {label!r}")

    return value


def validate_hostname(value: str) -> str:
    """校验单段主机名（不含 dots，1-63 字符，遵守 RFC 1123）。"""
    if isinstance(value, str) and "." in value:
        raise ValueError(f"{value!r} must not contain '.'")
    return validate_domain_name(
        value,
        allow_trailing_dot=False,
        allow_underscore=False,
        require_multi_label=False,
    )


def is_domain_name(value: object, **kwargs) -> bool:
    """`validate_domain_name` 的非抛出版本。"""

    if not isinstance(value, str):
        return False
    try:
        validate_domain_name(value, **kwargs)
    except ValueError:
        return False
    return True


_DN42_TLDS: frozenset[str] = frozenset({"dn42", "neo"})


def is_dn42_zone(value: str) -> bool:
    """是否为 dn42 命名空间下的 zone（`.dn42` 或 `.neo`，含 ENUM/IP6.ARPA）。

    这是一种宽松判定：除了直接的 `.dn42` / `.neo` 顶级，反向解析 zone
    （例如 `20.172.in-addr.arpa`、`2.4.d.f.ip6.arpa`）也属于 dn42 私网 DNS
    的常见场景，调用方可按需扩展。
    """

    if not is_domain_name(value):
        return False
    candidate = value.rstrip(".").lower()
    parts = candidate.split(".")
    if not parts:
        return False
    if parts[-1] in _DN42_TLDS:
        return True
    if candidate.endswith("in-addr.arpa") or candidate.endswith("ip6.arpa"):
        return True
    return False


__all__ = [
    "is_dn42_zone",
    "is_domain_name",
    "validate_domain_name",
    "validate_hostname",
]
