"""Local object-storage adapter + key layout (no S3 / network)."""

from __future__ import annotations

import pytest

from storage import episode_key, episode_prefix
from storage.local import LocalObjectStorage


def test_episode_key_layout():
    assert episode_prefix("u1", "e1") == "projects/u1/e1"
    assert episode_key("u1", "e1", "images", "01_temple.png") == (
        "projects/u1/e1/images/01_temple.png"
    )


@pytest.mark.anyio
async def test_local_storage_put_get_roundtrip(tmp_path):
    store = LocalObjectStorage(root=tmp_path, base_url="http://localhost:8000")
    key = episode_key("u1", "e1", "script.md")
    await store.put(key, b"hello world", content_type="text/markdown")
    assert await store.exists(key) is True
    assert await store.get(key) == b"hello world"
    url = await store.signed_url(key)
    assert url.endswith("/files/projects/u1/e1/script.md")
    await store.delete(key)
    assert await store.exists(key) is False


@pytest.mark.anyio
async def test_local_storage_blocks_traversal(tmp_path):
    store = LocalObjectStorage(root=tmp_path)
    with pytest.raises(ValueError):
        await store.put("../escape.txt", b"x")


@pytest.mark.anyio
async def test_local_storage_delete_prefix(tmp_path):
    """delete_prefix wipes a whole episode tree; only that prefix is removed."""
    store = LocalObjectStorage(root=tmp_path)
    # Episode e1 assets + an unrelated e2 asset that must survive.
    await store.put(episode_key("u1", "e1", "images", "01_a.png"), b"a")
    await store.put(episode_key("u1", "e1", "voice", "voice.mp3"), b"v")
    await store.put(episode_key("u1", "e1", "final.mp4"), b"f")
    await store.put(episode_key("u1", "e2", "final.mp4"), b"other")

    deleted = await store.delete_prefix(episode_prefix("u1", "e1"))
    assert deleted == 3
    assert await store.exists(episode_key("u1", "e1", "final.mp4")) is False
    assert await store.exists(episode_key("u1", "e1", "images", "01_a.png")) is False
    # Sibling episode untouched.
    assert await store.exists(episode_key("u1", "e2", "final.mp4")) is True
    # Idempotent: deleting a missing prefix is a no-op.
    assert await store.delete_prefix(episode_prefix("u1", "e1")) == 0


@pytest.fixture
def anyio_backend():
    return "asyncio"
