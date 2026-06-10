"""Health / readiness — platform-lead. No auth."""

from __future__ import annotations

from fastapi import APIRouter

from config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    s = get_settings()
    return {"status": "ok", "env": s.env, "storage_backend": s.storage_backend}
