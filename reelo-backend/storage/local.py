"""Local-filesystem object storage (dev).

Stores objects under ``STORAGE_LOCAL_ROOT/<key>``. ``signed_url`` returns a
URL served by the app's ``/files/{key}`` route (registered in
``web.routers.files``) — no real signing in dev, but the same call shape as S3.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote

from config import get_settings
from storage.base import ObjectStorage


class LocalObjectStorage(ObjectStorage):
    def __init__(self, root: str | Path | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        self.root = Path(root or settings.storage_local_root).resolve()
        self.base_url = (base_url or settings.base_url).rstrip("/")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Prevent path traversal outside the root.
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root)):
            raise ValueError(f"key escapes storage root: {key!r}")
        return p

    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        def _write() -> None:
            p = self._path(key)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)

        await asyncio.to_thread(_write)
        return key

    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        data = await asyncio.to_thread(Path(path).read_bytes)
        return await self.put(key, data, content_type=content_type)

    async def get(self, key: str) -> bytes:
        def _read() -> bytes:
            p = self._path(key)
            if not p.exists():
                raise FileNotFoundError(key)
            return p.read_bytes()

        return await asyncio.to_thread(_read)

    async def get_to_file(self, key: str, path: Path) -> Path:
        data = await self.get(key)
        dest = Path(path)

        def _write() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)

        await asyncio.to_thread(_write)
        return dest

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(lambda: self._path(key).exists())

    async def delete(self, key: str) -> None:
        def _del() -> None:
            p = self._path(key)
            if p.exists():
                p.unlink()

        await asyncio.to_thread(_del)

    async def signed_url(self, key: str, *, expires_in: int | None = None) -> str:
        # Dev: served by the app's /files route; no signing.
        return f"{self.base_url}/files/{quote(key)}"


__all__ = ["LocalObjectStorage"]
