"""Arq task skeletons.

These are the orchestration entrypoints the web process enqueues. Phase 1
provides the signatures, the ``CallContext`` construction seam, and logging;
the bodies raise ``NotImplementedError`` until Module 1 (script) and Module 2
(produce) fill them in.

Contract for module owners:
- ``produce_episode(ctx, user_id, episode_id)`` — Module 2 entrypoint. Ensures
  the episode is scripted (calls ``generate_script`` logic if needed), seeds the
  ``GenJob`` rows, runs voice ∥ N images → render → thumbnail, uploads assets,
  updates ``gen_jobs`` / episode status.
- ``generate_script(ctx, user_id, episode_id)`` — Module 1 lazy script gen.
  Derives segment_count, chunked RULE+parse+validate → segments + youtube meta,
  persists ``spec_json``, sets status ``scripted``.

Both build a :class:`clients.base.CallContext` from ``user_id`` (Module 3 §8).
``build_call_context`` is the shared seam; Module 3 will swap in DB-backed
``KeyStore`` / ``UsageLogger`` here.
"""

from __future__ import annotations

import logging

from clients.base import CallContext
from db.keystore_backend import (
    flush_usage,
    load_user_keystore,
    new_buffering_usage_logger,
)
from db.repository import ApiKeyRepo, UsageRepo
from db.session import session_scope
from keystore import KeyStore, build_cipher_from_settings
from usage import UsageLogger

log = logging.getLogger("reelo.worker")


async def build_call_context(ctx: dict, user_id: str) -> CallContext:
    """Construct the per-job :class:`CallContext` (Module 3 §8).

    DB-backed: preloads the user's encrypted ``api_keys`` into an in-memory
    snapshot so client reads stay sync, and uses a buffering usage logger whose
    events are flushed to ``usage_log`` by :func:`flush_call_context_usage` after
    the job. If the DB is unreachable (e.g. tests), falls back to an empty
    in-memory KeyStore so callers degrade gracefully.
    """
    cipher = build_cipher_from_settings()
    usage = new_buffering_usage_logger()
    try:
        async with session_scope() as session:
            keys = await load_user_keystore(ApiKeyRepo(session), cipher, user_id)
    except Exception as exc:  # noqa: BLE001 — DB unavailable: empty store
        log.warning("build_call_context: keystore preload failed (%s); using empty store", exc)
        keys = KeyStore(cipher)
    return CallContext(user_id=user_id, keys=keys, usage=usage)


async def flush_call_context_usage(call_ctx: CallContext) -> int:
    """Persist a job's buffered usage events to ``usage_log`` (call after a job)."""
    usage = call_ctx.usage
    if not isinstance(usage, UsageLogger):
        return 0
    async with session_scope() as session:
        return await flush_usage(UsageRepo(session), usage)


async def produce_episode(ctx: dict, user_id: str, episode_id: str) -> dict:
    """Module 2 entrypoint: materialize → assets → render → upload.

    Builds the per-user :class:`CallContext`, runs
    :func:`module2.runner.run_produce_episode` (step 0 ensure-scripted, voice ∥ N
    images, render + SRT, thumbnails, upload, status assets→assembled), then
    flushes buffered usage. Returns the runner's summary dict for logging/poll.
    """
    log.info("produce_episode received user_id=%s episode_id=%s", user_id, episode_id)
    # Imported lazily so the worker module imports cleanly without Module 2 deps.
    from module2.runner import run_produce_episode

    call_ctx = await build_call_context(ctx, user_id)
    try:
        return await run_produce_episode(user_id, episode_id, call_ctx)
    finally:
        try:
            await flush_call_context_usage(call_ctx)
        except Exception as exc:  # noqa: BLE001 — usage flush is best-effort
            log.warning("produce_episode: usage flush failed (%s)", exc)


async def generate_script(ctx: dict, user_id: str, episode_id: str) -> dict:
    """Module 1 entrypoint: lazy per-episode script generation (module-1 §7).

    Loads the series + episode from the DB, runs
    :func:`module1.episode_script.generate_episode_script`
    (derive segment_count → chunked RULE+parse+validate → segments + youtube),
    writes the updated episode back into ``spec_json`` (status→scripted), and
    flushes buffered usage. Idempotent: a scripted episode is a no-op.

    Returns:
        ``{"episode_id", "status", "segments": <count>}`` for logging/poll.
    """
    log.info("generate_script received user_id=%s episode_id=%s", user_id, episode_id)
    # Imported lazily so the worker module imports cleanly without Module 1 deps.
    from module1.episode_script import generate_episode_script
    from module1.persistence import find_series_for_episode, update_episode_in_series

    call_ctx = await build_call_context(ctx, user_id)
    try:
        async with session_scope() as session:
            found = await find_series_for_episode(session, user_id, episode_id)
            if found is None:
                raise ValueError(f"episode {episode_id} not found for user {user_id}")
            _, spec, ep = found

        if ep.segments:  # already scripted (§7 idempotency)
            return {"episode_id": episode_id, "status": ep.status, "segments": len(ep.segments)}

        updated = await generate_episode_script(spec, ep, call_ctx)

        async with session_scope() as session:
            await update_episode_in_series(session, user_id, spec.series_id, updated)

        return {
            "episode_id": episode_id,
            "status": updated.status,
            "segments": len(updated.segments),
        }
    finally:
        try:
            await flush_call_context_usage(call_ctx)
        except Exception as exc:  # noqa: BLE001 — usage flush is best-effort
            log.warning("generate_script: usage flush failed (%s)", exc)


__all__ = [
    "produce_episode",
    "generate_script",
    "build_call_context",
    "flush_call_context_usage",
]
