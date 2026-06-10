"""Arq worker configuration and the Redis connection used to enqueue jobs.

Run the worker with:  ``arq worker.settings.WorkerSettings``

The web process enqueues via :func:`enqueue_job` (see ``worker/enqueue.py``),
which shares the same :func:`redis_settings`.
"""

from __future__ import annotations

from arq.connections import RedisSettings

from config import get_settings


def redis_settings() -> RedisSettings:
    """Build Arq ``RedisSettings`` from ``REDIS_URL``."""
    return RedisSettings.from_dsn(get_settings().redis_url)


async def on_startup(ctx: dict) -> None:
    """Worker startup hook — place to warm shared resources (registry, storage)."""
    # Module 3 will attach a ServiceRegistry here; kept minimal for Phase 1.
    ctx["settings"] = get_settings()


async def on_shutdown(ctx: dict) -> None:
    from db.session import dispose_engine

    await dispose_engine()


class WorkerSettings:
    """Arq worker settings object (``arq worker.settings.WorkerSettings``)."""

    # Functions are imported here to register them with the worker.
    from worker.tasks import generate_script, produce_episode  # noqa: E402

    functions = [produce_episode, generate_script]
    redis_settings = redis_settings()
    on_startup = on_startup
    on_shutdown = on_shutdown
    max_jobs = get_settings().worker_max_jobs
    # Module 2 serializes episodes (M2-8/M2-9); render concurrency is capped
    # inside the task body, not here.


__all__ = ["WorkerSettings", "redis_settings", "on_startup", "on_shutdown"]
