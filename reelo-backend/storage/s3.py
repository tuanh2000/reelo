"""S3 / S3-compatible (MinIO, GCS-S3) object storage via aioboto3.

``aioboto3`` is imported lazily inside methods so this module imports cleanly
even when the dependency or credentials are absent (dev uses the local
backend). Configure via ``STORAGE_*`` env vars.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from config import get_settings
from storage.base import ObjectStorage


class S3ObjectStorage(ObjectStorage):
    def __init__(self) -> None:
        s = get_settings()
        self.bucket = s.storage_bucket
        self.region = s.storage_region
        self.endpoint_url = s.storage_endpoint_url_or_none
        # Browser-facing endpoint used only for presigned download URLs (falls back
        # to the internal endpoint). MinIO behind an internal hostname needs this so
        # signed URLs point at a host the browser can reach (signature is bound to
        # the host, so we must SIGN against the public endpoint — not rewrite later).
        self.public_endpoint_url = s.storage_public_endpoint_url_or_none
        self.access_key = s.storage_access_key_id or None
        self.secret_key = s.storage_secret_access_key or None
        self.signed_url_ttl = s.storage_signed_url_ttl

    @asynccontextmanager
    async def _client(self, *, endpoint_url: str | None = None) -> AsyncIterator[Any]:
        import aioboto3  # lazy import

        session = aioboto3.Session()
        async with session.client(
            "s3",
            region_name=self.region,
            endpoint_url=endpoint_url if endpoint_url is not None else self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
        ) as client:
            yield client

    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        async with self._client() as c:
            await c.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)
        return key

    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        async with self._client() as c:
            await c.upload_file(str(path), self.bucket, key, ExtraArgs=extra or None)
        return key

    async def get(self, key: str) -> bytes:
        async with self._client() as c:
            try:
                resp = await c.get_object(Bucket=self.bucket, Key=key)
            except c.exceptions.NoSuchKey as exc:  # type: ignore[attr-defined]
                raise FileNotFoundError(key) from exc
            async with resp["Body"] as stream:
                return await stream.read()

    async def get_to_file(self, key: str, path: Path) -> Path:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        async with self._client() as c:
            await c.download_file(self.bucket, key, str(dest))
        return dest

    async def exists(self, key: str) -> bool:
        async with self._client() as c:
            try:
                await c.head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:  # noqa: BLE001 — head raises ClientError(404)
                return False

    async def delete(self, key: str) -> None:
        async with self._client() as c:
            await c.delete_object(Bucket=self.bucket, Key=key)

    async def delete_prefix(self, prefix: str) -> int:
        """List + batch-delete every object under ``prefix`` (paginated)."""
        prefix = prefix.rstrip("/") + "/"
        deleted = 0
        async with self._client() as c:
            paginator = c.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
                if not objs:
                    continue
                # S3 DeleteObjects caps at 1000 keys/request; a page is already ≤1000.
                await c.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})
                deleted += len(objs)
        return deleted

    async def signed_url(self, key: str, *, expires_in: int | None = None) -> str:
        # Sign against the PUBLIC endpoint so the URL is both browser-reachable and
        # carries a signature valid for that host (defaults to the internal endpoint
        # when no public one is configured).
        async with self._client(endpoint_url=self.public_endpoint_url) as c:
            return await c.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in or self.signed_url_ttl,
            )


__all__ = ["S3ObjectStorage"]
