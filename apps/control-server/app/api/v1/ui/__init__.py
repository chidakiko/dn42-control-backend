from __future__ import annotations

"""WebUI 专用 BFF / 聚合 HTTP API。

挂在 ``/api/v1/ui`` 下,与 ``/api/v1/admin`` 平级。这里的端点都是为 WebUI 某个
界面「一次取全 / 服务端派生」而存在的组合视图(取代浏览器侧多次拉取 + 扒
``last_snapshot`` + 客户端算差分),并非通用资源接口——后者(``/admin`` 下的
``/routing/{fleet,summary,origins,prefixes,timeline}`` 等)留给对接其他系统用。

鉴权沿用 ``require_admin``:与 admin 同一把 admin token,未配置时 fail-closed(403)。
"""

from fastapi import APIRouter, Depends

from ...deps import require_admin
from . import observability, routing

router = APIRouter(prefix="/ui", tags=["ui"], dependencies=[Depends(require_admin)])
router.include_router(observability.router)
router.include_router(routing.router)


__all__ = ["router"]
