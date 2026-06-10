"""Object storage adapter. Use :func:`get_storage` for the configured backend."""

from __future__ import annotations

from functools import lru_cache

from storage.base import ObjectStorage, episode_key, episode_prefix


@lru_cache(maxsize=1)
def get_storage() -> ObjectStorage:
    """Return the configured object-storage backend (``local`` or ``s3``)."""
    from config import get_settings

    backend = get_settings().storage_backend
    if backend == "s3":
        from storage.s3 import S3ObjectStorage

        return S3ObjectStorage()
    from storage.local import LocalObjectStorage

    return LocalObjectStorage()


__all__ = ["ObjectStorage", "get_storage", "episode_key", "episode_prefix"]
