from __future__ import annotations

"""DN42 Control Server 入口。

本模块只负责装配 FastAPI app；具体路由 / 业务都在 ``app.api.v1`` 与
``app.services`` 下。

DB 引擎的生命周期由 ``lifespan`` 管理：
- 启动时建表（首次运行 / SQLite 临时库）+ seed bootstrap 节点；
- 退出时 dispose 引擎，释放连接池。
"""

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

# packages/dn42_schemas 仍按"源码即包"方式分发；保持现有 sys.path 注入。
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_SRC = _REPO_ROOT / "packages" / "dn42_schemas"
if str(_SCHEMA_SRC) not in sys.path:
    sys.path.insert(0, str(_SCHEMA_SRC))

from .api.v1 import api_router  # noqa: E402
from .core.config import ControlServerConfig  # noqa: E402
from .core.events import EventBus  # noqa: E402
from .db import Base, Database  # noqa: E402
from .services.audit import AuditLogStore  # noqa: E402
from .services.cache import Cache  # noqa: E402
from .services.desired_state import DesiredStateStore  # noqa: E402
from .services.enrollment import EnrollmentTokenStore  # noqa: E402
from .services.node_status import NodeStatusStore  # noqa: E402
from .services.pending_registrations import PendingRegistrationStore  # noqa: E402
from .services.routing import RoutingStore  # noqa: E402
from .services.tokens import TokenStore  # noqa: E402

_LOGGER = logging.getLogger("dn42.control")


def create_app(config: ControlServerConfig | None = None) -> FastAPI:
    """构造一个全新的 FastAPI 实例（便于测试隔离）。"""

    config = config or ControlServerConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(config.database_url)
        # MVP / 开发：首次启动时建表。生产部署应改用 alembic upgrade head。
        async with database.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # 生产启动**绝不**写入任何节点：空库由 import / provision 流程填充。
        # 测试需要预置节点时在 conftest 里显式 seed,不经此生产路径。

        cache = Cache(config.redis_url)
        if cache.enabled:
            _LOGGER.info("cache: Redis 缓存已启用 (%s)", config.redis_url)

        app.state.config = config
        app.state.database = database
        app.state.cache = cache
        app.state.tokens = TokenStore(database)
        app.state.desired_state = DesiredStateStore(database, cache=cache)
        app.state.node_status = NodeStatusStore(
            database,
            stale_after_seconds=config.health_stale_after_seconds,
            down_after_seconds=config.health_down_after_seconds,
            cache=cache,
        )
        app.state.pending_registrations = PendingRegistrationStore(database)
        app.state.enrollment_tokens = EnrollmentTokenStore(database)
        app.state.routing = RoutingStore(database, cache=cache)
        app.state.audit = AuditLogStore(database)
        app.state.bus = EventBus()

        try:
            yield
        finally:
            await cache.close()
            await database.dispose()

    app = FastAPI(title="DN42 Control Server", version="1.0.0", lifespan=lifespan)

    # 浏览器管理面（apps/web）跨源直连 admin API：放行配置的源 + Authorization 头。
    # allow_credentials 与通配 "*" 不兼容，故仅在非通配时开启。
    if config.cors_origins:
        allow_all = "*" in config.cors_origins
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"] if allow_all else list(config.cors_origins),
            allow_credentials=not allow_all,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router, prefix="/api/v1")

    _ADMIN_PREFIX = "/api/v1/admin"
    _AUDITED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

    @app.middleware("http")
    async def audit_admin_writes(request: Request, call_next) -> Response:
        """记录所有到达 Admin API 的写请求（含鉴权失败的尝试）。"""

        response = await call_next(request)
        if (
            request.url.path.startswith(_ADMIN_PREFIX)
            and request.method in _AUDITED_METHODS
        ):
            audit: AuditLogStore = request.app.state.audit
            await audit.record(
                actor=getattr(request.state, "admin_actor", None),
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                detail={"query": str(request.url.query)} if request.url.query else {},
            )
        return response

    @app.get("/healthz", tags=["health"])
    async def healthz(request: Request) -> Response:
        """存活 + DB 连通性探针。

        DB 不可达时返回 503，让 compose / systemd / 负载均衡的健康检查能真实
        反映服务可用性，而不是 DB 挂了仍报 ok。
        """

        database: Database = request.app.state.database
        try:
            async with database.session() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - 探针把任何 DB 故障都视作不健康
            _LOGGER.error("healthz: database probe failed: %s", exc)
            return JSONResponse(
                status_code=503, content={"status": "unavailable", "database": "down"}
            )
        return JSONResponse(content={"status": "ok", "database": "up"})

    return app


app = create_app()


__all__ = ["app", "create_app"]
