"""Generation pipeline (Module 2: reelo-video-generator).

``POST /generation/start`` ensures the episode is scripted (enqueues Module 1's
lazy script gen if needed), seeds a parent ``gen_jobs`` row, enqueues the Arq
``produce_episode`` task, and returns ``{jobId, costEstimate}``. The UI polls
``GET /generation/{jobId}`` for the child ``GenJob[]``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from db.repository import ApiKeyRepo, GenJobRepo
from module1.persistence import find_series_for_episode
from module2 import jobs as jobmod
from storage import get_storage
from web._provider_keys import series_readiness
from web.deps import CurrentUser, DbSession
from web.schemas import (
    PollGenerationResponse,
    StartGenerationRequest,
    StartGenerationResponse,
)
from worker.enqueue import enqueue_job

router = APIRouter(prefix="/generation", tags=["generation"])


@router.post("/start", response_model=StartGenerationResponse)
async def start_generation(
    body: StartGenerationRequest, user_id: CurrentUser, db: DbSession
) -> StartGenerationResponse:
    """Ensure scripted, seed parent job, enqueue produce → ``{jobId, costEstimate}``.

    - Scripting is owned by EXACTLY ONE path: the worker's ``produce_episode`` step
      0 (``ensure_scripted``). We must NOT also enqueue ``generate_script`` here:
      both would run script generation concurrently, and because the LLM isn't
      deterministic the two scripts differ — one ends up in ``spec_json`` while the
      images are generated from the other, so every asset is later seen as
      "changed" and regenerated (orphaned old images). The workspace already runs
      its own ``generate_script`` for the "đang viết kịch bản" UI and gates the
      Produce button until the script is done, so a normal produce finds segments
      and step 0 is a no-op.
    - The returned ``jobId`` is the parent ``gen_jobs`` id the UI polls.
    """
    found = await find_series_for_episode(db, user_id, body.episode_id)
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"episode {body.episode_id} not found",
        )
    _, spec, ep = found

    # Per-series readiness gate: the chosen script/image/voice providers must have
    # the user's per-user keys (and a voice sample for OmniVoice clone) before we
    # spend compute. 409 with a clear message so the UI can route to the key page.
    present = {r.key_ref for r in await ApiKeyRepo(db).list_refs(user_id)}
    sr, ir, vr, missing = series_readiness(spec, present)
    if not (sr and ir and vr):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Series chưa đủ điều kiện sản xuất: "
                + " ".join(missing)
                + " Vào trang Cấu hình AI để thêm key (hoặc tải giọng mẫu)."
            ),
        )

    estimate = jobmod.cost_estimate(spec, ep)
    parent_id = await jobmod.seed_parent(GenJobRepo(db), user_id, ep)

    # Single script-gen owner: produce's ensure_scripted (step 0). Do NOT also
    # enqueue generate_script here — concurrent script gens desync spec_json from
    # the generated images (see docstring).
    await enqueue_job("produce_episode", user_id, body.episode_id)

    return StartGenerationResponse(job_id=parent_id, cost_estimate=estimate)


@router.get("/{job_id}", response_model=PollGenerationResponse)
async def poll_generation(
    job_id: str, user_id: CurrentUser, db: DbSession
) -> PollGenerationResponse:
    """Return the child ``GenJob[]`` for the parent job's episode (pure read)."""
    repo = GenJobRepo(db)
    parent = await repo.get(user_id, job_id)
    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"job {job_id} not found"
        )
    children = await repo.children_for_episode(user_id, parent.episode_id)
    jobs = [jobmod.row_to_genjob(c) for c in children]
    # Attach a signed preview URL to each finished image so the produce screen can
    # show pictures as they land in storage (incremental upload per segment).
    previews = await jobmod.image_preview_urls(
        user_id, parent.episode_id, children, get_storage()
    )
    for j in jobs:
        j.preview_url = previews.get(j.id)
    # Surface the parent's seed time (server clock) so the UI can anchor the
    # produce elapsed counter to server time instead of a client mount timer.
    created = getattr(parent, "created_at", None)
    started_at = created.isoformat() if created is not None else None
    return PollGenerationResponse(jobs=jobs, started_at=started_at)


@router.post("/{job_id}/retry/{child_id}", response_model=PollGenerationResponse)
async def retry_child(
    job_id: str, child_id: str, user_id: CurrentUser, db: DbSession
) -> PollGenerationResponse:
    """Re-run one failed child job + re-trigger produce (re-render).

    Marks the child ``queued`` and re-enqueues ``produce_episode``; the runner is
    idempotent on existing assets (M2-10), so it only regenerates what is missing
    and re-renders. Returns the refreshed child list.
    """
    repo = GenJobRepo(db)
    parent = await repo.get(user_id, job_id)
    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"job {job_id} not found"
        )
    child = await repo.get(user_id, child_id)
    if child is None or child.parent_id != job_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"child {child_id} not found under job {job_id}",
        )
    child.state = "queued"
    child.progress = 0
    child.stderr = None
    await db.flush()

    await enqueue_job("produce_episode", user_id, parent.episode_id)

    children = await repo.children_for_episode(user_id, parent.episode_id)
    return PollGenerationResponse(jobs=[jobmod.row_to_genjob(c) for c in children])


__all__ = ["router"]
