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
    """Worker startup hook — warm shared resources + sweep zombie jobs.

    After a (re)start nothing from a previous worker process is still running, so
    any gen_job left ``running``/``paused`` is a zombie (crash / OOM / redeploy /
    a job_timeout that cancelled the task before it could record the error). We
    flip those to ``error`` so the UI never spins on a dead job. Best-effort: a DB
    hiccup here must not stop the worker from booting.
    """
    import logging

    ctx["settings"] = get_settings()
    try:
        from module2.runner import reconcile_stale_jobs

        flipped = await reconcile_stale_jobs()
        if flipped:
            logging.getLogger("reelo.worker").info(
                "on_startup: marked %d stale running/paused job(s) as error", flipped
            )
    except Exception as exc:  # noqa: BLE001 — startup sweep is best-effort
        logging.getLogger("reelo.worker").warning("on_startup: stale-job sweep failed (%s)", exc)


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
    # Per-job wall-clock cap (default 600s): long multi-chunk scripts can exceed
    # arq's 300s default. The provider-level fail-fast (clients/claude_cli.py)
    # ensures one wedged CLI call can't consume this whole budget.
    job_timeout = get_settings().worker_job_timeout
    # No arq-level auto-retry by default (max_tries=1): tasks retry internally and
    # surface errors on the episode, so re-running a hung/failed job only
    # multiplied the user's wait (the old 3×300s ≈ 15min) for nothing.
    max_tries = get_settings().worker_max_tries
    # Module 2 serializes episodes (M2-8/M2-9); render concurrency is capped
    # inside the task body, not here.


__all__ = ["WorkerSettings", "redis_settings", "on_startup", "on_shutdown"]
