"""Episode endpoints (Module 1 script + Module 2 image curation, M2-12).

``POST /episodes/{id}/script`` triggers lazy per-episode script generation. Per
integration §6 the default is to **enqueue** the Arq ``generate_script`` task and
return the current episode (the UI then polls / re-fetches once ``scripted``).
Idempotent: if segments already exist, return them without enqueueing.

``GET /episodes/{id}/image-candidates`` + ``POST /episodes/{id}/image-selection``
implement human curation of media candidates for web-* media providers. The grid
**mixes** real photos (``web-commons``, keyless) with real video clips
(``web-pexels``, BYOK) when the episode's image provider is ``web`` (aggregate) or
a single ``web-*`` provider (M2-12 / M2-13). They are no-ops (409) for generative
providers, which keep the auto luồng with no selection step.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from clients.base import CallContext
from clients.registry import get_registry
from db.keystore_backend import load_user_keystore
from db.repository import ApiKeyRepo, EpisodeRepo
from keystore import build_cipher_from_settings
from module1.persistence import find_series_for_episode
from module2 import curation as cur
from usage import UsageLogger
from web.deps import CurrentUser, DbSession
from web.schemas import (
    EpisodeScriptResponse,
    ImageCandidatesResponse,
    ImageSelectionRequest,
)
from worker.enqueue import enqueue_job

router = APIRouter(prefix="/episodes", tags=["episodes"])


async def _media_ctx(user_id: str, db: DbSession) -> CallContext:
    """A :class:`CallContext` with the user's keys preloaded for candidate search.

    web-commons is keyless, but web-pexels (video clips) needs the user's Pexels
    key to be present so it (a) reports available and (b) can search. Usage is
    recorded on a discarded logger (candidate search is free / 0-cost).
    """
    cipher = build_cipher_from_settings()
    keys = await load_user_keystore(ApiKeyRepo(db), cipher, user_id)
    return CallContext(user_id=user_id, keys=keys, usage=UsageLogger())


@router.post("/{episode_id}/script", response_model=EpisodeScriptResponse)
async def generate_episode_script(
    episode_id: str, user_id: CurrentUser, db: DbSession
) -> EpisodeScriptResponse:
    """Lazy per-episode script gen. Enqueues ``generate_script``; idempotent.

    - If the episode already has segments → return it (no enqueue).
    - Otherwise enqueue the worker task and return the current episode shell; the
      worker fills segments + youtube + status→scripted, which the UI re-fetches.
    """
    found = await find_series_for_episode(db, user_id, episode_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"episode {episode_id} not found"
        )
    _, _, ep = found
    if not ep.segments:
        await enqueue_job("generate_script", user_id, episode_id)
    return EpisodeScriptResponse(episode=ep)


# --------------------------------------------------------------------------- #
# Image curation (M2-12) — web-photo candidate selection                      #
# --------------------------------------------------------------------------- #
@router.get("/{episode_id}/image-candidates", response_model=ImageCandidatesResponse)
async def get_image_candidates(
    episode_id: str, user_id: CurrentUser, db: DbSession
) -> ImageCandidatesResponse:
    """Per-segment real-photo candidate grids for human curation (web-photo only).

    - 404 if the episode is missing; 409 if it isn't scripted yet or the image
      provider is generative (AI providers have no selection step — produce直接).
    - First call: search ~9 candidates/segment, cache into ``Episode.image_curation``
      with ``chosen_id`` defaulting to the first candidate, and return it.
    - Subsequent calls: return the cache (no re-search), so choices are stable.
    """
    found = await find_series_for_episode(db, user_id, episode_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"episode {episode_id} not found"
        )
    _, spec, ep = found
    if not ep.segments:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="episode is not scripted yet — generate the script first",
        )

    registry = get_registry()
    provider_id = cur.image_provider_id(spec.providers)
    if not cur.provider_supports_candidates(registry, provider_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"image provider '{provider_id}' is automatic — no media selection "
                "step (only web-* providers like web-commons / web-pexels / web need "
                "curation)"
            ),
        )

    repo = EpisodeRepo(db)
    curation = await repo.get_curation(user_id, episode_id)
    if not curation:
        ctx = await _media_ctx(user_id, db)
        # Query every web-* media provider the user can use (web-commons photos
        # always; web-pexels clips only with a Pexels key) and merge per segment.
        clients = await cur.web_media_providers(registry, provider_id, ctx)
        curation = await cur.build_curation(
            registry, clients, ep, ctx, size=spec.image_style.aspect
        )
        await repo.set_curation(user_id, episode_id, curation)

    return ImageCandidatesResponse.model_validate(curation)


@router.post("/{episode_id}/image-selection", response_model=ImageCandidatesResponse)
async def save_image_selection(
    episode_id: str, body: ImageSelectionRequest, user_id: CurrentUser, db: DbSession
) -> ImageCandidatesResponse:
    """Apply ``{segment_index: candidate_id}`` to the cached curation; return state.

    - 404 if the episode is missing; 409 if no candidates were generated yet
      (call ``GET /image-candidates`` first).
    - 400 if any candidate_id does not belong to that segment's cached list.
    """
    repo = EpisodeRepo(db)
    curation = await repo.get_curation(user_id, episode_id)
    if not curation:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no image candidates yet — GET /image-candidates first",
        )
    updated, invalid = cur.apply_selection(curation, body.selections)
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid candidate selection for segment(s): {invalid}",
        )
    await repo.set_curation(user_id, episode_id, updated)
    return ImageCandidatesResponse.model_validate(updated)


__all__ = ["router"]
