"""Global runtime controls shared between the web process and the Arq worker.

A tiny Redis-backed flag set by the web (``POST /settings/voice-pause``) and polled
by the worker between voice chunks. **Global** (not per-user / per-episode): a
single GTX-class GPU runs OmniVoice, so when several videos are produced at once
the user can pause ALL voice synthesis (let the remote image generation finish
first) without cooking the card — then resume. Image/render are unaffected.

Redis is the right store here (vs ``episode.paths`` like the per-episode script
cancel): the flag is process-global, read very frequently while paused, and needs
no migration. ``ArqRedis`` (the pool the web + worker already hold) is a
``redis.asyncio`` client, so plain ``get``/``set``/``delete`` work.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("reelo.worker.control")

# Redis key holding the global voice-pause flag ("1" = paused; absent = running).
VOICE_PAUSE_KEY = "reelo:voice:paused"


async def is_voice_paused(redis: Any) -> bool:
    """Return True if voice synthesis is globally paused. False on any read error.

    Best-effort: a Redis hiccup must never wedge the worker — degrade to "not
    paused" so production keeps moving rather than blocking forever.
    """
    if redis is None:
        return False
    try:
        return bool(await redis.get(VOICE_PAUSE_KEY))
    except Exception as exc:  # noqa: BLE001 — degrade to not-paused
        log.warning("voice-pause read failed (%s); treating as not paused", exc)
        return False


async def set_voice_paused(redis: Any, paused: bool) -> None:
    """Set (``paused=True``) or clear the global voice-pause flag."""
    if paused:
        await redis.set(VOICE_PAUSE_KEY, "1")
    else:
        await redis.delete(VOICE_PAUSE_KEY)


__all__ = ["VOICE_PAUSE_KEY", "is_voice_paused", "set_voice_paused"]
