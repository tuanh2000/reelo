"""Enqueue helpers used by the web process to push jobs onto the Arq queue.

The web layer never runs heavy work; it calls :func:`enqueue_job` and returns a
``jobId`` the UI polls. A single Arq Redis pool is created lazily and reused.
"""

from __future__ import annotations

from typing import Any

from arq import create_pool
from arq.connections import ArqRedis

from worker.settings import redis_settings

_pool: ArqRedis | None = None


async def get_arq_pool() -> ArqRedis:
    """Return the shared Arq Redis pool, creating it on first use."""
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def enqueue_job(function: str, *args: Any, **kwargs: Any) -> str:
    """Enqueue an Arq task and return its job id.

    Example::

        job_id = await enqueue_job("produce_episode", user_id, episode_id)

    Raises:
        RuntimeError: if Arq declines to enqueue (e.g. duplicate job id).
    """
    pool = await get_arq_pool()
    job = await pool.enqueue_job(function, *args, **kwargs)
    if job is None:
        raise RuntimeError(f"failed to enqueue job {function!r} (already queued?)")
    return job.job_id


async def close_arq_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
    _pool = None


__all__ = ["get_arq_pool", "enqueue_job", "close_arq_pool"]
