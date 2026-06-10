"""Serve local-storage objects in dev (platform-lead).

Backs :meth:`storage.local.LocalObjectStorage.signed_url` so the same
``signed_url`` call shape works in dev as with S3 presigned URLs. In ``s3``
mode this route is harmless (storage returns real presigned URLs instead).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from config import get_settings
from pathlib import Path

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{key:path}")
async def serve_file(key: str) -> FileResponse:
    settings = get_settings()
    if settings.storage_backend != "local":
        raise HTTPException(status_code=404, detail="Not served in this storage mode")
    root = Path(settings.storage_local_root).resolve()
    target = (root / key).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(target)
