from __future__ import annotations

"""健康检查。"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


__all__ = ["router"]
