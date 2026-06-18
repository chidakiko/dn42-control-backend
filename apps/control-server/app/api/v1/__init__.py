from __future__ import annotations

"""控制服务器 v1 API 聚合路由。"""

from fastapi import APIRouter

from . import admin, agent_http, agent_ws, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(agent_http.router)
api_router.include_router(agent_ws.router)
api_router.include_router(admin.router)


__all__ = ["api_router"]
