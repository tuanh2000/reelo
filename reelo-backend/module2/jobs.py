"""Generation-job seeding, projection, and cost estimation (Module 2 §8/§9).

The produce pipeline seeds one parent ``gen_jobs`` row + child rows the UI polls:
``voice`` + ``image_1..N`` + ``render`` + ``thumbnail`` (M2 §8). Children are
projected to the UI-facing :class:`models.jobs.GenJob` via :func:`row_to_genjob`.

Cost estimate (§9) is computed from the scripted episode (N images, voice chars,
3 thumbnails) using the provider pricing in ``services.yaml`` — shown before a
produce run so the user can confirm (important for long/Dense videos, D3).

These helpers compose the platform-lead's ``GenJobRepo`` / ORM ``GenJobRow``
(shapes untouched — contract held); only Module 2 calls them.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from clients.registry import ServiceRegistry, get_registry
from db.models import GenJobRow
from db.repository import GenJobRepo
from models.jobs import GenJob
from models.spec import EpisodeSpec, SeriesSpec
from web.schemas import CostEstimate

# Coarse progress milestones (M2 §8): truthful, not fine-grained.
PROGRESS_QUEUED = 0
PROGRESS_START = 10
PROGRESS_RUNNING_CAP = 90
PROGRESS_DONE = 100

# lucide icon ids per kind (UI GenJob.icon)
ICONS: dict[str, str] = {
    "parent": "film",
    "voice": "mic",
    "image": "image",
    "render": "film",
    "thumbnail": "image",
}


def new_job_id(prefix: str = "job") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def row_to_genjob(row: GenJobRow) -> GenJob:
    """Project a child ``GenJobRow`` onto the UI-facing :class:`GenJob`."""
    return GenJob(
        id=row.id,
        name=row.name,
        icon=row.icon or ICONS.get(row.kind, "circle"),
        state=row.state,  # type: ignore[arg-type]
        progress=row.progress,
        # Only surface captured stderr for failed jobs (copyable in the UI).
        stderr=row.stderr if row.state == "error" else None,
    )


@dataclass(frozen=True)
class SeededJobs:
    """Ids of the seeded parent + child jobs (so the runner can update them)."""

    parent_id: str
    voice_id: str
    image_ids: list[str]
    render_id: str
    thumbnail_id: str


async def seed_parent(repo: GenJobRepo, user_id: str, ep: EpisodeSpec) -> str:
    """Insert the parent ``gen_jobs`` row (queued) and return its id.

    Created by ``POST /generation/start`` so a ``jobId`` exists for polling before
    the worker has scripted/seeded the children. Safe to call before the episode
    is scripted (no segments needed).
    """
    parent_id = new_job_id("parent")
    await repo.add(
        GenJobRow(
            id=parent_id,
            user_id=user_id,
            episode_id=ep.episode_id,
            parent_id=None,
            kind="parent",
            name=ep.title or "Episode",
            icon=ICONS["parent"],
            state="queued",
            progress=PROGRESS_QUEUED,
        )
    )
    return parent_id


_IMAGE_NAME_RE = re.compile(r"Image (\d+):")


def _image_index(name: str) -> int:
    """Parse the segment index out of an image child's name (``"Image 3: …"``).

    Image children are zipped with ``ep.segments`` IN ORDER by the runner
    (:func:`module2.runner.run_images`), so a reused child set must come back in
    segment-index order — hence we sort by the index embedded in the name.
    """
    m = _IMAGE_NAME_RE.match(name or "")
    return int(m.group(1)) if m else 0


def _reuse_children(
    existing: list[GenJobRow], ep: EpisodeSpec, parent_id: str
) -> SeededJobs | None:
    """Map an existing child set back onto :class:`SeededJobs`, or ``None``.

    Reuse only when the shape matches the current episode exactly — one voice,
    one render, one thumbnail, and one image per segment. A mismatch (e.g. the
    script was re-generated with a different segment count) returns ``None`` so
    the caller seeds a fresh set instead of zipping the wrong job ids.
    """
    voice = [r for r in existing if r.kind == "voice"]
    images = [r for r in existing if r.kind == "image"]
    render = [r for r in existing if r.kind == "render"]
    thumb = [r for r in existing if r.kind == "thumbnail"]
    if not (
        len(voice) == 1
        and len(render) == 1
        and len(thumb) == 1
        and len(images) == len(ep.segments)
        and images
    ):
        return None
    images.sort(key=lambda r: _image_index(r.name))
    return SeededJobs(
        parent_id=parent_id,
        voice_id=voice[0].id,
        image_ids=[r.id for r in images],
        render_id=render[0].id,
        thumbnail_id=thumb[0].id,
    )


async def seed_children(
    repo: GenJobRepo, user_id: str, ep: EpisodeSpec, parent_id: str
) -> SeededJobs:
    """Insert (or reuse) the child ``gen_jobs`` rows under ``parent_id``.

    One ``image`` child per segment (N — D3, no cap). Called by the runner after
    step 0 (ensure scripted), since the image-job count depends on the segments.

    **Idempotent:** a resume / retry re-enqueues ``produce_episode``, which calls
    this again for the SAME parent. When a matching child set already exists we
    reuse it (returning its ids) instead of inserting a duplicate set — so the UI
    never shows doubled "Voiceover" / "Image N" rows on a re-run.
    """
    existing = [
        r
        for r in await repo.children_for_episode(user_id, ep.episode_id)
        if r.parent_id == parent_id
    ]
    reused = _reuse_children(existing, ep, parent_id)
    if reused is not None:
        return reused

    def _child(kind: str, name: str) -> GenJobRow:
        return GenJobRow(
            id=new_job_id(kind),
            user_id=user_id,
            episode_id=ep.episode_id,
            parent_id=parent_id,
            kind=kind,
            name=name,
            icon=ICONS.get(kind, "circle"),
            state="queued",
            progress=PROGRESS_QUEUED,
        )

    voice = _child("voice", "Voiceover")
    await repo.add(voice)

    image_rows: list[GenJobRow] = []
    for s in ep.segments:
        row = _child("image", f"Image {s.index}: {s.image_label}")
        await repo.add(row)
        image_rows.append(row)

    render = _child("render", "Render video")
    await repo.add(render)
    thumb = _child("thumbnail", "Thumbnails")
    await repo.add(thumb)

    return SeededJobs(
        parent_id=parent_id,
        voice_id=voice.id,
        image_ids=[r.id for r in image_rows],
        render_id=render.id,
        thumbnail_id=thumb.id,
    )


def _image_preview_key(user_id: str, episode_id: str, name: str) -> str | None:
    """Storage key for an image child's PNG, reconstructed from its display name.

    A child is named ``f"Image {index}: {image_label}"`` and its file is
    ``images/{image_filename(index, image_label)}.png`` — so the key is derivable
    from the name alone (no episode-spec load on the hot poll path). Returns
    ``None`` if the name isn't an image-child name.
    """
    m = _IMAGE_NAME_RE.match(name or "")
    if not m:
        return None
    index = int(m.group(1))
    parts = (name or "").split(": ", 1)
    label = parts[1] if len(parts) == 2 else ""
    from storage import episode_key

    from module2.materialize import image_filename

    return episode_key(
        user_id, episode_id, "images", f"{image_filename(index, label)}.png"
    )


async def image_preview_urls(
    user_id: str, episode_id: str, rows: list[GenJobRow], storage
) -> dict[str, str]:
    """Map ``{image_job_id: signed_url}`` for every ``done`` image child.

    Lets the produce screen preview each picture as it lands in storage (assets are
    uploaded incrementally per segment). Cheap: ``signed_url`` only mints a URL (no
    network / no existence check) — a missing/video asset simply 404s in the
    browser, which the UI hides. Best-effort: any storage hiccup yields no preview
    for that job rather than failing the poll.
    """
    out: dict[str, str] = {}
    for r in rows:
        if getattr(r, "kind", None) != "image" or getattr(r, "state", None) != "done":
            continue
        key = _image_preview_key(user_id, episode_id, r.name)
        if key is None:
            continue
        try:
            out[r.id] = await storage.signed_url(key)
        except Exception:  # noqa: BLE001 — preview is best-effort
            continue
    return out


async def find_parent_for_episode(
    repo: GenJobRepo, user_id: str, episode_id: str
) -> GenJobRow | None:
    """Return the most-recent parent ``gen_jobs`` row for an episode, if any.

    The runner uses this to attach children to the parent ``POST /generation/start``
    seeded. Iterates children-or-all rows; relies on ``created_at`` ordering.
    """
    res = await repo.s.execute(
        _parent_query(user_id, episode_id)
    )
    rows = list(res.scalars().all())
    return rows[-1] if rows else None


def _parent_query(user_id: str, episode_id: str):  # noqa: ANN202 - sqlalchemy Select
    from sqlalchemy import select

    return (
        select(GenJobRow)
        .where(
            GenJobRow.episode_id == episode_id,
            GenJobRow.user_id == user_id,
            GenJobRow.parent_id.is_(None),
        )
        .order_by(GenJobRow.created_at)
    )


# --------------------------------------------------------------------------- #
# Cost estimate (§9)                                                          #
# --------------------------------------------------------------------------- #
def estimate_voice_chars(ep: EpisodeSpec) -> int:
    """Total narration characters (proxy for TTS billing units)."""
    return sum(len(s.narration) for s in ep.segments)


def _per_image_price(reg: ServiceRegistry, provider: str) -> float:
    client = reg.try_get(provider)
    if client is None:
        return 0.0
    block = (client.config.pricing or {}).get("generate-image", {}) or {}
    try:
        return float(block.get("per_image", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _per_1k_chars_price(reg: ServiceRegistry, provider: str) -> float:
    client = reg.try_get(provider)
    if client is None:
        return 0.0
    block = (client.config.pricing or {}).get("generate-voice", {}) or {}
    try:
        return float(block.get("per_1k_chars", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def cost_estimate(
    series: SeriesSpec, ep: EpisodeSpec, *, registry: ServiceRegistry | None = None
) -> CostEstimate:
    """Estimate the produce cost: N segment images + 3 thumbnails + voice chars (§9).

    Uses ``services.yaml`` pricing for the selected providers. ``estimated_cost``
    is ``None`` when both prices are zero (free path) so the UI can show "free".
    """
    reg = registry or get_registry()
    if ep.segments:
        n_images = len(ep.segments)
        voice_chars = estimate_voice_chars(ep)
    else:
        # Not scripted yet: derive N and voice chars from target_minutes × density
        # (Module 1's formulas) so the UI can show an estimate up front (§9).
        from module1.prompt import derive_segment_count, derive_word_budget

        target = ep.target_minutes or series.target_minutes
        n_images = derive_segment_count(target, series.density)
        budget = derive_word_budget(target, series.language, n_images)
        # ~6 characters per word is a rough cross-language average for billing.
        voice_chars = budget.total_words * 6

    image_provider = series.providers.get("image", "kie")
    voice_provider = series.voice.provider

    img_price = _per_image_price(reg, image_provider)
    voice_price = _per_1k_chars_price(reg, voice_provider)

    image_cost = (n_images + 3) * img_price  # segments + 3 thumbnail candidates
    voice_cost = (voice_chars / 1000.0) * voice_price
    total = image_cost + voice_cost

    note_bits: list[str] = [f"{n_images} images + 3 thumbnails", f"{voice_chars} voice chars"]
    estimated = total if total > 0 else None
    if estimated is None:
        note_bits.append("free providers")
    return CostEstimate(
        images=n_images,
        voice_chars=voice_chars,
        estimated_cost=round(estimated, 4) if estimated is not None else None,
        note="; ".join(note_bits),
    )


__all__ = [
    "PROGRESS_QUEUED",
    "PROGRESS_START",
    "PROGRESS_RUNNING_CAP",
    "PROGRESS_DONE",
    "ICONS",
    "new_job_id",
    "row_to_genjob",
    "SeededJobs",
    "seed_parent",
    "seed_children",
    "image_preview_urls",
    "find_parent_for_episode",
    "estimate_voice_chars",
    "cost_estimate",
]
