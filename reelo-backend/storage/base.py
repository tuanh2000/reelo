"""Object-storage adapter contract.

Layout convention (integration §5): ``projects/<user_id>/<episode_id>/...``.
The worker renders to local temp, uploads via :meth:`put` / :meth:`put_file`,
the DB stores object keys, and the web serves files through :meth:`signed_url`.

:func:`episode_prefix` / :func:`episode_key` build canonical keys so every
module addresses assets the same way.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


def episode_prefix(user_id: str, episode_id: str) -> str:
    """Canonical key prefix for an episode's assets."""
    return f"projects/{user_id}/{episode_id}"


def episode_key(user_id: str, episode_id: str, *parts: str) -> str:
    """Build a full object key under an episode prefix.

    Example: ``episode_key(u, e, "images", "01_temple.png")`` ->
    ``projects/<u>/<e>/images/01_temple.png``.
    """
    suffix = "/".join(p.strip("/") for p in parts if p)
    return f"{episode_prefix(user_id, episode_id)}/{suffix}" if suffix else episode_prefix(
        user_id, episode_id
    )


class ObjectStorage(ABC):
    """Abstract object store. Implementations must be safe to share per-process."""

    @abstractmethod
    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        """Store bytes at ``key``. Returns the stored key."""

    @abstractmethod
    async def put_file(self, key: str, path: Path, *, content_type: str | None = None) -> str:
        """Upload a local file to ``key``. Returns the stored key."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Fetch the object's bytes. Raises ``FileNotFoundError`` if absent."""

    @abstractmethod
    async def get_to_file(self, key: str, path: Path) -> Path:
        """Download ``key`` to a local file. Returns the local path."""

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def signed_url(self, key: str, *, expires_in: int | None = None) -> str:
        """Return a time-limited URL the browser can fetch directly."""


__all__ = ["ObjectStorage", "episode_prefix", "episode_key"]
