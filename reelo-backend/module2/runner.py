"""``produce_episode`` orchestration (Module 2 §8/§14) — the Arq task body.

Pipeline for one episode (serialized; one episode/run, M2-8/M2-9):

    0. ensure scripted   — if segments empty, call Module 1 generate_episode_script
    1. materialize       — spec → local temp project folder (script.md + prompts)
    2. voice ∥ N images  — asyncio.gather; images bounded by a Semaphore (3-4)
    3. invariant check   — any image still missing → block render, parent error (M2-7)
    4. render            — render.py (Ken Burns + xfade + ducking) → final.mp4
    5. subtitles         — subs.srt (folded into the render job, M2-2)
    6. thumbnail         — 3 candidates (best-effort)
    7. upload            — push the whole project folder to object storage
    8. status            — episode assets → assembled; persist paths; flush usage

Job state lives in ``gen_jobs`` (Postgres) so the UI can poll; progress is coarse
and truthful (queued 0 / start 10 / running ≤90 / done 100). DB / storage / Module 1
access goes through the platform-lead's session + repos + storage adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path

from clients.base import CallContext, ImageRequest, ProviderUnavailableError, Task
from clients.registry import ServiceRegistry, get_registry
from db.repository import EpisodeRepo, GenJobRepo
from db.session import session_scope
from models.spec import EpisodeSpec, SeriesSpec
from module1.persistence import find_series_for_episode, update_episode_in_series
from storage import episode_key, get_storage

from module2 import curation as curation_mod
from module2 import jobs as jobmod
from module2 import materialize as mat
from module2 import render as renderer
from module2 import subtitles, thumbnail, voice
from module2.materialize import ProjectLayout

log = logging.getLogger("reelo.module2.runner")

IMAGE_CONCURRENCY = 3  # M2-8 (3-4 parallel image calls)


# --------------------------------------------------------------------------- #
# Small job-state helpers (operate inside a caller-provided session)          #
# --------------------------------------------------------------------------- #
async def _set_state(
    repo: GenJobRepo,
    user_id: str,
    job_id: str,
    *,
    state: str | None = None,
    progress: int | None = None,
    stderr: str | None = None,
) -> None:
    row = await repo.get(user_id, job_id)
    if row is None:
        return
    if state is not None:
        row.state = state
    if progress is not None:
        row.progress = progress
    if stderr is not None:
        row.stderr = stderr[-4000:]
    await repo.s.flush()


async def update_job(
    user_id: str,
    job_id: str,
    *,
    state: str | None = None,
    progress: int | None = None,
    stderr: str | None = None,
) -> None:
    """Open a short session and update one job row (used between async stages)."""
    async with session_scope() as session:
        await _set_state(
            GenJobRepo(session), user_id, job_id,
            state=state, progress=progress, stderr=stderr,
        )


# --------------------------------------------------------------------------- #
# Stages                                                                      #
# --------------------------------------------------------------------------- #
async def ensure_scripted(
    user_id: str, episode_id: str, ctx: CallContext
) -> tuple[SeriesSpec, EpisodeSpec]:
    """Step 0: load the series/episode; if not scripted, run Module 1 (lazy gen).

    Returns the (series, scripted-episode) pair. Raises ``ValueError`` if the
    episode cannot be found.
    """
    async with session_scope() as session:
        found = await find_series_for_episode(session, user_id, episode_id)
        if found is None:
            raise ValueError(f"episode {episode_id} not found for user {user_id}")
        _, spec, ep = found

    if ep.segments:
        return spec, ep

    from module1.episode_script import generate_episode_script

    updated = await generate_episode_script(spec, ep, ctx)
    async with session_scope() as session:
        spec2 = await update_episode_in_series(session, user_id, spec.series_id, updated)
    return spec2, updated


async def run_images(
    series: SeriesSpec,
    ep: EpisodeSpec,
    lo: ProjectLayout,
    ctx: CallContext,
    image_job_ids: list[str],
    user_id: str,
    *,
    registry: ServiceRegistry,
    concurrency: int = IMAGE_CONCURRENCY,
    curation: dict | None = None,
) -> list[BaseException | None]:
    """Generate one PNG per segment in parallel (Semaphore-bounded).

    Each segment's image job is marked running→done/error individually. Returns a
    list parallel to segments: ``None`` on success, the exception on failure (so
    the caller can block render and mark the parent error, M2-7).

    For web-* providers with a human ``curation`` blob (M2-12/M2-13) the segment's
    **chosen** candidate is downloaded (``download_chosen``) — a photo (image PNG)
    or a video clip (mp4) depending on the candidate's ``media_type``. The chosen
    candidate's *source* provider (web-commons / web-pexels) is resolved per
    segment from the curation blob, since the merged grid mixes sources. Segments
    lacking a choice — or any non-curated / generative provider — fall back to the
    auto ``generate_image`` path so a missing choice never hard-blocks a render.

    The per-segment media kind + path is recorded in
    ``ctx.extra["media_plan"][index] = {"media_type", "path"}`` so the orchestrator
    feeds the renderer the right file + dispatch (image Ken Burns vs video clip).
    """
    provider = series.providers.get("image", "kie")
    # ``web`` (aggregate) / a single web-* provider both have a curation step; for
    # the auto fallback path resolve a concrete provider. ``web`` aggregate has no
    # single client, so default the auto fallback to web-commons (keyless photos).
    auto_provider = "web-commons" if provider == curation_mod.WEB_AGGREGATE else provider
    auto_client = await registry.resolve(Task.GENERATE_IMAGE, auto_provider, ctx)
    sem = asyncio.Semaphore(max(1, concurrency))

    # Shared per-episode dedup set + attribution sink for web-photo providers
    # (web-commons). Generative providers ignore both. Threaded via ctx.extra so
    # the client can read the "already used" titles and report attribution.
    if not isinstance(ctx.extra, dict):
        ctx.extra = {}
    ctx.extra.setdefault("commons_used", set())
    credits: dict[int, dict] = {}
    # index -> {"media_type": "image"|"video", "path": Path}
    media_plan: dict[int, dict] = {}

    async def _one(seg, job_id: str) -> BaseException | None:
        await update_job(user_id, job_id, state="running", progress=jobmod.PROGRESS_START)
        img_out = lo.image_png(seg.index, seg.image_label)
        prompt_file = lo.image_txt(seg.index, seg.image_label)
        async with sem:
            try:
                # Curated path: download the user-chosen candidate for this segment
                # using the candidate's OWN source provider (mixed grid, M2-13).
                chosen = curation_mod.chosen_candidate_for(curation, seg.index)
                src_provider = curation_mod.chosen_provider_for(curation, seg.index)
                media_type = "image"
                out_path = img_out
                if chosen is not None and src_provider is not None:
                    src_client = registry.try_get(src_provider)
                    if src_client is None:
                        raise ProviderUnavailableError(
                            f"curation referenced unknown provider {src_provider!r}"
                        )
                    if chosen.is_video:
                        media_type = "video"
                        out_path = lo.media_mp4(seg.index, seg.image_label)
                    result = await src_client.download_chosen(chosen, out_path, ctx)
                    if isinstance(ctx.extra, dict):
                        ctx.extra["commons_used"].add(chosen.id)
                else:
                    result = await auto_client.generate_image(
                        ImageRequest(
                            prompt_file=prompt_file,
                            size=series.image_style.aspect,
                            query=seg.image_query or mat.deslug(seg.image_label),
                            label=seg.image_label,
                        ),
                        out_path=img_out,
                        ctx=ctx,
                    )
                media_plan[seg.index] = {
                    "media_type": media_type,
                    "path": result.out_path,
                }
                # Capture attribution (web-* providers) for credits.json.
                attribution = (result.raw or {}).get("attribution") if result.raw else None
                if attribution:
                    credits[seg.index] = {
                        "index": seg.index,
                        "media_type": media_type,
                        "file": result.out_path.name,
                        **attribution,
                    }
                await update_job(user_id, job_id, state="done", progress=jobmod.PROGRESS_DONE)
                return None
            except Exception as exc:  # noqa: BLE001 — recorded per-job, blocks render
                log.warning("media segment %d failed: %s", seg.index, exc)
                await update_job(
                    user_id, job_id, state="error",
                    progress=jobmod.PROGRESS_QUEUED, stderr=str(exc),
                )
                return exc

    errors = await asyncio.gather(
        *[_one(seg, jid) for seg, jid in zip(ep.segments, image_job_ids)]
    )
    # Stash collected attribution + the media plan where the orchestrator reads them.
    ctx.extra["commons_credits"] = [credits[k] for k in sorted(credits)]
    ctx.extra["media_plan"] = media_plan
    return errors


async def run_voice(
    series: SeriesSpec,
    lo: ProjectLayout,
    ctx: CallContext,
    voice_job_id: str,
    user_id: str,
    *,
    registry: ServiceRegistry,
) -> voice.VoiceOutcome:
    """Run the voice stage, updating the voice job row around it."""
    await update_job(user_id, voice_job_id, state="running", progress=jobmod.PROGRESS_START)
    try:
        outcome = await voice.synth_voice(series, lo, ctx, registry=registry)
    except Exception as exc:  # noqa: BLE001
        await update_job(
            user_id, voice_job_id, state="error",
            progress=jobmod.PROGRESS_QUEUED, stderr=str(exc),
        )
        raise
    await update_job(user_id, voice_job_id, state="done", progress=jobmod.PROGRESS_DONE)
    return outcome


async def upload_project(user_id: str, episode_id: str, lo: ProjectLayout) -> dict[str, str]:
    """Upload the whole project folder to object storage; return key paths.

    Walks the local folder and uploads every file under
    ``projects/<user>/<episode>/<relpath>``. Returns a small map of the key
    asset keys (final/srt/thumbnails) the episode row and export need.
    """
    storage = get_storage()
    root = lo.root
    final_key = ""
    srt_key = ""
    credits_key = ""
    thumb_keys: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        key = episode_key(user_id, episode_id, *rel.split("/"))
        await storage.put_file(key, path)
        if rel == "final.mp4":
            final_key = key
        elif rel == "subs.srt":
            srt_key = key
        elif rel == "credits.json":
            credits_key = key
        elif rel.startswith("thumbnails/"):
            thumb_keys.append(key)
    paths: dict[str, str] = {}
    if final_key:
        paths["final"] = final_key
    if srt_key:
        paths["srt"] = srt_key
    if credits_key:
        paths["credits"] = credits_key
    if thumb_keys:
        paths["thumbnails"] = ",".join(sorted(thumb_keys))
    paths["prefix"] = episode_key(user_id, episode_id)
    return paths


async def _save_episode_assets(
    user_id: str, series_id: str, episode_id: str, paths: dict[str, str]
) -> None:
    """Persist asset keys + flip status assets→assembled (via EpisodeRepo)."""
    async with session_scope() as session:
        await EpisodeRepo(session).set_paths(
            user_id, episode_id, paths, status="assembled", merge=True
        )


async def _load_curation(user_id: str, episode_id: str) -> dict | None:
    """Read the episode's ``image_curation`` blob (M2-12), or None if absent.

    Missing curation is non-fatal — run_images falls back to the auto path — so a
    DB hiccup here never blocks a render.
    """
    try:
        async with session_scope() as session:
            return await EpisodeRepo(session).get_curation(user_id, episode_id)
    except Exception as exc:  # noqa: BLE001 — curation is best-effort
        log.warning("could not load image_curation for %s: %s", episode_id, exc)
        return None


# --------------------------------------------------------------------------- #
# Orchestration                                                               #
# --------------------------------------------------------------------------- #
async def run_produce_episode(
    user_id: str,
    episode_id: str,
    ctx: CallContext,
    *,
    registry: ServiceRegistry | None = None,
    work_root: Path | None = None,
) -> dict:
    """Full produce pipeline for one episode. The worker entrypoint calls this.

    Args:
        user_id: tenant id.
        episode_id: episode to produce.
        ctx: per-user :class:`CallContext` (BYOK + usage).
        registry: override the process registry (tests).
        work_root: override the temp root (tests); a unique subfolder is created.

    Returns:
        ``{"episode_id", "status", "images", "duration_s", "paths"}``.

    Raises:
        RuntimeError: if any segment image fails after retry (render blocked, M2-7).
        ValueError: if the episode is missing.
    """
    reg = registry or get_registry()
    started = time.monotonic()

    spec, ep = await ensure_scripted(user_id, episode_id, ctx)

    # Attach children to the parent that POST /generation/start seeded (or create
    # one if the runner is invoked directly, e.g. tests). Children depend on the
    # segment count, so they are seeded here (after step 0).
    async with session_scope() as session:
        repo = GenJobRepo(session)
        parent = await jobmod.find_parent_for_episode(repo, user_id, episode_id)
        parent_id = parent.id if parent is not None else await jobmod.seed_parent(
            repo, user_id, ep
        )
        seeded = await jobmod.seed_children(repo, user_id, ep, parent_id)
    await update_job(
        user_id, seeded.parent_id, state="running", progress=jobmod.PROGRESS_START
    )
    # Reflect "đang sản xuất" on the episode itself (assets) so the project screen
    # badge + the workspace stage recovery (GET /episodes/{id}) show progress even
    # for a user who navigated away. Flipped to assembled at the end (best-effort:
    # a stale scripted status must never block the produce pipeline).
    try:
        async with session_scope() as session:
            await EpisodeRepo(session).set_status(user_id, episode_id, "assets")
    except Exception as exc:  # noqa: BLE001 — status surfacing is best-effort
        log.warning("could not mark episode %s assets: %s", episode_id, exc)

    # Resolve a work folder. Keep the whole project folder (M2-10).
    base = Path(work_root) if work_root else Path(tempfile.gettempdir()) / "reelo-produce"
    base.mkdir(parents=True, exist_ok=True)
    proj_root = base / f"{user_id}_{episode_id}"

    # Optional background music: download series.music.path to music/bg.mp3.
    music_src = await _fetch_music(spec, proj_root)

    lo = mat.materialize(spec, ep, proj_root, music_src=music_src)

    # Human image-curation (M2-12) for web-photo providers: load the user's chosen
    # candidates so run_images downloads them instead of auto-picking. None for AI
    # providers / un-curated episodes (run_images then uses the auto path).
    curation = await _load_curation(user_id, episode_id)

    # Voice ∥ N images.
    voice_task = asyncio.create_task(
        run_voice(spec, lo, ctx, seeded.voice_id, user_id, registry=reg)
    )
    images_task = asyncio.create_task(
        run_images(
            spec, ep, lo, ctx, seeded.image_ids, user_id, registry=reg,
            concurrency=IMAGE_CONCURRENCY, curation=curation,
        )
    )
    voice_outcome, image_errors = await asyncio.gather(voice_task, images_task)

    # Image failure → block render (M2-7): mark render + parent error and stop.
    failures = [e for e in image_errors if e is not None]
    if failures:
        msg = f"{len(failures)} image(s) failed; render blocked (M2-7)"
        await update_job(
            user_id, seeded.render_id, state="error",
            progress=jobmod.PROGRESS_QUEUED, stderr=msg,
        )
        await update_job(
            user_id, seeded.parent_id, state="error",
            progress=jobmod.PROGRESS_QUEUED, stderr=msg,
        )
        raise RuntimeError(msg)

    # Persist image/clip attribution (web-* providers) so publish/export can show
    # credit — a legal requirement for a SaaS reusing CC-BY/PD photos + Pexels
    # clips (M2-11 / M2-13).
    _write_credits(lo, ctx)

    # Resolve each segment's chosen media (image PNG or video mp4) from the plan
    # run_images recorded; segments that fell through to the auto path default to
    # the image PNG. This is media-aware (mixed photos + clips, M2-13).
    media_plan = ctx.extra.get("media_plan", {}) if isinstance(ctx.extra, dict) else {}
    media_paths, media_types = _resolve_media(ep, lo, media_plan)

    # Belt-and-braces: enforce the count invariant before render (media-aware).
    _verify_media_invariant(ep, lo, media_paths)

    # Render (+ SRT folded in).
    await update_job(
        user_id, seeded.render_id, state="running", progress=jobmod.PROGRESS_START
    )
    try:
        narrations = [s.narration for s in ep.segments]
        await renderer.render_episode(
            media_paths,
            narrations,
            lo.voice_mp3,
            lo.final_mp4,
            spec.image_style.aspect,
            media_types=media_types,
            music_path=lo.music_bg if lo.music_bg.exists() else None,
            work_dir=lo.root / "clips",
        )
        subtitles.write_srt(narrations, voice_outcome.duration_s, lo.subs_srt)
    except Exception as exc:  # noqa: BLE001
        await update_job(
            user_id, seeded.render_id, state="error",
            progress=jobmod.PROGRESS_QUEUED, stderr=str(exc),
        )
        await update_job(
            user_id, seeded.parent_id, state="error",
            progress=jobmod.PROGRESS_QUEUED, stderr=str(exc),
        )
        raise
    await update_job(
        user_id, seeded.render_id, state="done", progress=jobmod.PROGRESS_DONE
    )

    # Thumbnails (best-effort; never blocks the episode).
    await update_job(
        user_id, seeded.thumbnail_id, state="running", progress=jobmod.PROGRESS_START
    )
    try:
        await thumbnail.generate_thumbnails(spec, ep.title, lo, ctx, registry=reg)
        await update_job(
            user_id, seeded.thumbnail_id, state="done", progress=jobmod.PROGRESS_DONE
        )
    except Exception as exc:  # noqa: BLE001 — thumbnails optional
        log.warning("thumbnails failed (non-blocking): %s", exc)
        await update_job(
            user_id, seeded.thumbnail_id, state="error",
            progress=jobmod.PROGRESS_QUEUED, stderr=str(exc),
        )

    # Upload + persist + status.
    paths = await upload_project(user_id, episode_id, lo)
    await _save_episode_assets(user_id, spec.series_id, episode_id, paths)
    await update_job(
        user_id, seeded.parent_id, state="done", progress=jobmod.PROGRESS_DONE
    )

    elapsed = time.monotonic() - started
    log.info("produce_episode done in %.1fs (%d images)", elapsed, len(ep.segments))
    return {
        "episode_id": episode_id,
        "status": "assembled",
        "images": len(ep.segments),
        "duration_s": round(voice_outcome.duration_s, 2),
        "paths": paths,
    }


def _resolve_media(
    ep: EpisodeSpec, lo: ProjectLayout, media_plan: dict
) -> tuple[list[Path], list[str]]:
    """Per-segment (path, media_type) for the renderer, in segment order.

    Uses the plan ``run_images`` recorded (chosen photo PNG or video mp4); a
    segment missing from the plan (auto path / fallback) defaults to its image PNG.
    """
    paths: list[Path] = []
    kinds: list[str] = []
    for s in ep.segments:
        entry = media_plan.get(s.index) if isinstance(media_plan, dict) else None
        if entry and entry.get("media_type") == "video":
            paths.append(Path(entry["path"]))
            kinds.append("video")
        elif entry and entry.get("path"):
            paths.append(Path(entry["path"]))
            kinds.append("image")
        else:
            paths.append(lo.image_png(s.index, s.image_label))
            kinds.append("image")
    return paths, kinds


def _verify_media_invariant(
    ep: EpisodeSpec, lo: ProjectLayout, media_paths: list[Path]
) -> None:
    """Media-aware count/section invariant: each segment has a present media file.

    Like :func:`materialize.verify_invariant` but a segment's file may be a video
    mp4 (not just an image PNG), so it checks the resolved per-segment paths.
    """
    n_seg = len(ep.segments)
    n_sec = mat.count_sections(lo.script_md)
    if n_sec != n_seg:
        raise mat.MaterializeInvariantError(
            f"script.md has {n_sec} sections but episode has {n_seg} segments"
        )
    present = [p for p in media_paths if Path(p).exists()]
    if len(present) != n_seg:
        missing = [Path(p).name for p in media_paths if not Path(p).exists()]
        raise mat.MaterializeInvariantError(
            f"expected {n_seg} media files, found {len(present)} (missing: {missing})"
        )


def _write_credits(lo: ProjectLayout, ctx: CallContext) -> None:
    """Write ``credits.json`` from web-photo attribution gathered in ctx.extra.

    No-op when the image provider is generative (no attribution collected). The
    file is uploaded with the rest of the project folder so publish/export can
    surface per-image credit (title/author/license/source_url).
    """
    credits = ctx.extra.get("commons_credits") if isinstance(ctx.extra, dict) else None
    if not credits:
        return
    (lo.root / "credits.json").write_text(
        json.dumps({"images": credits}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _fetch_music(spec: SeriesSpec, proj_root: Path) -> Path | None:
    """Download ``series.music.path`` (object key) to a local file, if set."""
    music = spec.music or {}
    key = music.get("path")
    if not key:
        return None
    dest = proj_root / "_music_src.mp3"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        await get_storage().get_to_file(key, dest)
        return dest
    except Exception as exc:  # noqa: BLE001 — missing music is non-fatal
        log.warning("music fetch failed for %s: %s", key, exc)
        return None


__all__ = [
    "IMAGE_CONCURRENCY",
    "ensure_scripted",
    "run_images",
    "run_voice",
    "upload_project",
    "run_produce_episode",
    "update_job",
]
