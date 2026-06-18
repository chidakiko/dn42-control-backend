from __future__ import annotations

"""管理面 (admin) HTTP API。

将 Node / Peering / WgInterface / BgpSession / DnsZone / Token 这 6 类资源的
CRUD 接口聚合到 ``/api/v1/admin`` 下。每个子模块只负责自己资源的路由；
写操作统一遵守 ``_helpers`` 的事务纪律：CRUD 变更与 ``materialize_change``
在同一事务（失败整体回滚），提交后才 ``broadcast_change`` 广播。

整个前缀挂 ``require_admin``：未配置 admin token 时 fail-closed（403），配置后
所有请求必须携带 ``Authorization: Bearer <admin token>``。
"""

from fastapi import APIRouter, Depends

from ...deps import require_admin
from . import (
    audit,
    bgp_sessions,
    dns_zones,
    enrollment_tokens,
    health,
    interfaces,
    nodes,
    peerings,
    provision,
    registrations,
    routing,
    tokens,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
router.include_router(nodes.router)
router.include_router(peerings.router)
router.include_router(interfaces.router)
router.include_router(bgp_sessions.router)
router.include_router(dns_zones.router)
router.include_router(tokens.router)
router.include_router(enrollment_tokens.router)
router.include_router(provision.router)
router.include_router(health.router)
router.include_router(registrations.router)
router.include_router(routing.router)
router.include_router(audit.router)


__all__ = ["router"]
