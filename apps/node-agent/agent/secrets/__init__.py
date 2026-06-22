from __future__ import annotations

"""节点本地密钥管理：WG 私钥的生成/持久化、托管封装、apply 时注入。"""

from .wireguard import (
    SECRET_REF_SCHEME,
    build_wireguard_key_report,
    ensure_node_private_key,
    is_secret_ref,
    push_wireguard_key_to_container,
)

__all__ = [
    "SECRET_REF_SCHEME",
    "build_wireguard_key_report",
    "ensure_node_private_key",
    "is_secret_ref",
    "push_wireguard_key_to_container",
]
