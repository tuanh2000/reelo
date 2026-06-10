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
from db.repository import ApiKeyRepo, EpisodeRepo, GenJobRepo
from keystore import build_cipher_from_settings
from models.jobs import GenJob
from module1.persistence import find_series_for_episode, reset_episode_to_outline
from module2 import curation as cur
from storage import episode_prefix, get_storage
from usage import UsageLogger
from web.deps import CurrentUser, DbSession
from web.schemas import (
    EpisodeAssets,
    EpisodeDetailResponse,
    EpisodeResetResponse,
    EpisodeScriptResponse,
    GenerationLookup,
    ImageCandidatesResponse,
    ImageSelectionRequest,
)
from worker.enqueue import enqueue_job

router = APIRouter(prefix="/episodes", tags=["episodes"])


def _child_to_genjob(row: object) -> GenJob:
    """Project a child ``gen_jobs`` row onto the UI-facing :class:`GenJob`.

    A small local projection (instead of importing Module 2's ``row_to_genjob``)
    so the episodes router stays free of Module 2 deps. Surfaces ``stderr`` only
    for failed jobs, mirroring the poll endpoint.
    """
    state = getattr(row, "state", "queued")
    return GenJob(
        id=getattr(row, "id"),
        name=getattr(row, "name", ""),
        icon=getattr(row, "icon", "") or "circle",
        state=state,  # type: ignore[arg-type]
        progress=getattr(row, "progress", 0) or 0,
        stderr=getattr(row, "stderr", None) if state == "error" else None,
    )


def _generation_lookup(parent: object, children: list) -> GenerationLookup:
    """Summarize the most-recent produce job for state recovery.

    ``state`` is derived from the children (the parent row's own state can lag):
    "error" if any child errored, "done" if there are children and all are
    terminal-done, else "running" (work in flight or not yet seeded). With no
    children we fall back to the parent's own state. ``started_at`` is the parent's
    ``created_at`` ISO timestamp (``None`` in fakes without one).
    """
    jobs = [_child_to_genjob(c) for c in children]
    if jobs:
        if any(j.state == "error" for j in jobs):
            state = "error"
        elif all(j.state == "done" for j in jobs):
            state = "done"
        else:
            state = "running"
    else:
        parent_state = getattr(parent, "state", "queued")
        state = parent_state if parent_state in ("running", "done", "error") else "running"
    created = getattr(parent, "created_at", None)
    started_at = created.isoformat() if created is not None else None
    return GenerationLookup(
        job_id=getattr(parent, "id"),
        state=state,  # type: ignore[arg-type]
        started_at=started_at,
        jobs=jobs,
    )


async def _media_ctx(user_id: str, db: DbSession) -> CallContext:
    """A :class:`CallContext` with the user's keys preloaded for candidate search.

    web-commons is keyless, but web-pexels (video clips) needs the user's Pexels
    key to be present so it (a) reports available and (b) can search. Usage is
    recorded on a discarded logger (candidate search is free / 0-cost).
    """
    cipher = build_cipher_from_settings()
    keys = await load_user_keystore(ApiKeyRepo(db), cipher, user_id)
    return CallContext(user_id=user_id, keys=keys, usage=UsageLogger())


@router.get("/{episode_id}", response_model=EpisodeDetailResponse)
async def get_episode(
    episode_id: str, user_id: CurrentUser, db: DbSession
) -> EpisodeDetailResponse:
    """Fetch one episode's current spec + signed asset URLs (poll / review source).

    - 404 if the episode is missing.
    - ``episode`` reflects live ``status`` / ``segments`` / ``youtube`` (the UI
      polls this after lazy script gen and after produce).
    - ``assets`` carries signed URLs (``videoUrl`` / ``srtUrl`` / ``thumbnails``)
      once the runner has written ``paths`` (final / srt / thumbnails); empty
      otherwise. Signed URLs are minted fresh per call (they expire).
    """
    found = await find_series_for_episode(db, user_id, episode_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"episode {episode_id} not found"
        )
    series_row, _, ep = found

    ep_row = await EpisodeRepo(db).get(user_id, episode_id)
    paths = (ep_row.paths or {}) if ep_row is not None else {}
    storage = get_storage()
    assets = EpisodeAssets()
    if paths.get("final"):
        assets.video_url = await storage.signed_url(paths["final"])
    if paths.get("srt"):
        assets.srt_url = await storage.signed_url(paths["srt"])
    thumb_keys = [t for t in (paths.get("thumbnails") or "").split(",") if t]
    assets.thumbnails = [await storage.signed_url(t) for t in thumb_keys]

    # Surface lazy script-gen progress (running/done/error + copyable message) so
    # the workspace can show state instead of an infinite spinner. If segments
    # already exist, the script is effectively done regardless of any stale flag.
    script_status, script_error = EpisodeRepo.script_state(paths)
    script_started_at = EpisodeRepo.script_started_at(paths)
    if ep.segments:
        script_status, script_error, script_started_at = "done", None, None

    # Recover the most-recent produce job so the workspace can rebuild its "đang
    # sản xuất" view from the backend (no client-held jobId). None if never run.
    gen_repo = GenJobRepo(db)
    parent = await gen_repo.latest_parent_for_episode(user_id, episode_id)
    generation: GenerationLookup | None = None
    if parent is not None:
        children = await gen_repo.children_for_episode(user_id, episode_id)
        generation = _generation_lookup(parent, children)

    return EpisodeDetailResponse(
        series_id=series_row.id,
        episode=ep,
        assets=assets,
        script_status=script_status,  # type: ignore[arg-type]
        script_error=script_error,
        script_started_at=script_started_at,
        generation=generation,
    )


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
        # Mark running + clear any prior error *before* enqueueing so a re-fetch
        # (or a "Thử lại" after a failure) immediately reflects the new attempt,
        # not the stale error — even before the worker picks the job up.
        await EpisodeRepo(db).set_script_state(user_id, episode_id, "running")
        await enqueue_job("generate_script", user_id, episode_id)
    return EpisodeScriptResponse(episode=ep)


@router.post("/{episode_id}/reset", response_model=EpisodeResetResponse)
async def reset_episode(
    episode_id: str, user_id: CurrentUser, db: DbSession
) -> EpisodeResetResponse:
    """Destructive "làm lại từ đầu": reset an episode to outline-only draft.

    Wipes everything produced for the episode so the user can re-script + re-produce
    fresh, scoped by ``user_id``:

    1. **spec** — clears ``segments`` + ``youtube`` in the series ``spec_json``,
       keeping the outline (title/order/desc/target_minutes); ``status`` → ``draft``.
    2. **status / curation** — clears ``script_status``/``script_error``/
       ``script_started_at`` and ``image_curation`` (in ``paths``/the row).
    3. **jobs** — deletes the episode's gen_jobs (parent + children).
    4. **storage** — deletes ``projects/<user>/<episode>/`` (images/voice/final/
       thumbnails) AND the resume hash manifest (held in ``paths``, cleared in 2) so
       a later produce can NOT reuse a stale image/voice for the new script.

    404 if the episode is missing. Returns the reset episode + cleanup counts.
    """
    found = await find_series_for_episode(db, user_id, episode_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"episode {episode_id} not found"
        )
    series_row, spec, _ = found

    # 3. Delete gen_jobs (parent + children) for a clean slate next produce.
    jobs_deleted = await GenJobRepo(db).delete_for_episode(user_id, episode_id)

    # 1. spec → outline-only draft. 2. Clear row-level paths/urls/curation +
    #    script status (incl. the resume asset_manifest held in ``paths``).
    reset = await reset_episode_to_outline(db, user_id, spec.series_id, episode_id)
    if reset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"episode {episode_id} not found"
        )
    await EpisodeRepo(db).reset_to_draft(user_id, episode_id)

    # 4. Delete the episode's whole storage prefix (best-effort: the DB reset is the
    #    source of truth; a storage hiccup must not leave the episode un-resettable).
    assets_deleted = 0
    try:
        assets_deleted = await get_storage().delete_prefix(
            episode_prefix(user_id, episode_id)
        )
    except Exception:  # noqa: BLE001 — storage cleanup is best-effort
        assets_deleted = 0

    return EpisodeResetResponse(
        episode=reset, jobs_deleted=jobs_deleted, assets_deleted=assets_deleted
    )


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
