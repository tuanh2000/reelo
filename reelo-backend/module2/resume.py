"""Resume / idempotent produce — skip assets already generated & still valid.

A produce run can be re-triggered (a deploy/reboot kills the worker mid-run, a
``retry`` after a partial failure, or the user simply re-produces). Image
providers may be **paid** (kie.ai) and voice (ElevenLabs) costs money, so blindly
regenerating every asset burns credit on work already done. This module makes
produce idempotent: for each asset, if the **same content** has already been
materialized to object storage, the run reuses it instead of regenerating.

Correctness over thrift — we only skip when the asset is BOTH present in storage
AND its **content hash matches**. The hash captures everything that determines
the asset's bytes:

- **image (generative)**: the composed prompt (preset + style_layer + segment
  ``image_prompt``) + aspect + the auto search ``query``/``label``. If the script
  rewrites a segment, the prompt changes → hash changes → regenerate.
- **image (web-curated)**: the chosen candidate id + its source provider. If the
  user re-curates a different photo/clip, the hash changes → re-download.
- **voice**: the full narration join + provider + voice_id + clone sample key.
  Any script edit changes the narration → re-synthesize.
- **thumbnail**: episode title + the style inputs (base_prompt/style_layer/palette
  /aspect). Title or style change → regenerate the candidates.

The manifest (``index -> hash``) lives in ``episode.paths["asset_manifest"]``
(JSONB, no migration). It is read at the start of a run and rewritten at the end
with the hashes that actually correspond to the assets now in storage. On any
doubt (cannot read the manifest, cannot download the cached file, hash missing)
we fall through to regenerate — never reuse a stale asset.

``reset`` (the destructive "làm lại từ đầu" path) deletes this manifest along with
the storage assets, so a fresh script can never reuse an old image.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from models.spec import EpisodeSpec, SeriesSpec
from storage import episode_key, get_storage

from module2 import curation as curation_mod
from module2 import materialize as mat
from module2.materialize import ProjectLayout

log = logging.getLogger("reelo.module2.resume")

# Key under ``episode.paths`` (JSONB) holding the per-asset content-hash manifest.
MANIFEST_KEY = "asset_manifest"


def _sha(*parts: str) -> str:
    """Stable short hex digest of the given text parts (order matters)."""
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8"))
        h.update(b"\x1e")  # record separator so "ab"+"c" != "a"+"bc"
    return h.hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Content hashes (one per asset kind)                                         #
# --------------------------------------------------------------------------- #
def image_hash(
    series: SeriesSpec,
    seg,
    *,
    curation: dict | None = None,
) -> str:
    """Content hash for a segment's image/clip — generative prompt OR chosen pick.

    Web-curated segments hash the chosen candidate id + source provider (so a
    re-curation invalidates); every other segment hashes the composed generative
    prompt + aspect + the auto query/label the runner would search on.
    """
    chosen = curation_mod.chosen_candidate_for(curation, seg.index)
    src_provider = curation_mod.chosen_provider_for(curation, seg.index)
    if chosen is not None and src_provider is not None:
        return _sha("curated", src_provider, chosen.id)
    prompt = mat.compose_image_prompt(series, seg.image_prompt)
    query = seg.image_query or mat.deslug(seg.image_label)
    return _sha(
        "generative",
        series.providers.get("image", "kie"),
        series.image_style.aspect,
        prompt,
        query,
        seg.image_label,
    )


def voice_hash(series: SeriesSpec, ep: EpisodeSpec) -> str:
    """Content hash for the voiceover — narration join + provider + voice config."""
    narration = mat.SECTION_SEP.join(s.narration.strip() for s in ep.segments)
    voice = series.voice
    sample_key = ""
    if voice.voice_sample is not None:
        sample_key = voice.voice_sample.audio_key or ""
    return _sha(
        "voice",
        voice.provider,
        voice.voice_id,
        voice.mode,
        sample_key,
        series.language,
        narration,
    )


def thumbnail_hash(series: SeriesSpec, ep: EpisodeSpec) -> str:
    """Content hash for the thumbnail set — title + style inputs."""
    style = series.image_style
    return _sha(
        "thumbnail",
        series.providers.get("image", "kie"),
        ep.title,
        style.base_prompt,
        style.style_layer or "",
        ",".join(style.palette),
        style.aspect,
    )


# --------------------------------------------------------------------------- #
# Manifest read/write helpers                                                 #
# --------------------------------------------------------------------------- #
def read_manifest(paths: dict | None) -> dict:
    """Project the ``asset_manifest`` blob out of an episode's ``paths`` (or {})."""
    man = (paths or {}).get(MANIFEST_KEY)
    return dict(man) if isinstance(man, dict) else {}


def build_manifest(
    series: SeriesSpec, ep: EpisodeSpec, *, curation: dict | None = None
) -> dict:
    """Compute the full content-hash manifest for the current spec + curation.

    Shape::

        {"images": {"1": <hash>, ...}, "voice": <hash>, "thumbnail": <hash>}
    """
    return {
        "images": {str(s.index): image_hash(series, s, curation=curation) for s in ep.segments},
        "voice": voice_hash(series, ep),
        "thumbnail": thumbnail_hash(series, ep),
    }


# --------------------------------------------------------------------------- #
# Cached-asset fetch (download an unchanged asset into the work folder)       #
# --------------------------------------------------------------------------- #
async def _try_fetch(user_id: str, episode_id: str, rel: str, dest: Path) -> bool:
    """Download ``projects/<u>/<e>/<rel>`` into ``dest`` if it exists. True on hit.

    Any error (missing key, storage hiccup) → False so the caller regenerates.
    """
    storage = get_storage()
    key = episode_key(user_id, episode_id, *rel.split("/"))
    try:
        if not await storage.exists(key):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        await storage.get_to_file(key, dest)
        return dest.exists() and dest.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001 — any doubt → regenerate
        log.warning("resume: could not fetch cached %s: %s", key, exc)
        return False


async def reuse_segment_image(
    user_id: str,
    episode_id: str,
    seg,
    lo: ProjectLayout,
    *,
    want_hash: str,
    prev: dict,
    curation: dict | None,
) -> str | None:
    """Reuse a segment's cached image/clip when present + unchanged.

    Returns the media kind reused (``"image"`` | ``"video"``) and leaves the file
    in the work folder, or ``None`` when nothing valid is cached (→ regenerate).
    Compares the wanted hash against the previous run's per-segment hash; only when
    they match do we even look in storage.
    """
    prev_images = (prev or {}).get("images") or {}
    if prev_images.get(str(seg.index)) != want_hash:
        return None
    # Decide which file the cached pick is: a video clip (curated) or a PNG.
    chosen = curation_mod.chosen_candidate_for(curation, seg.index)
    if chosen is not None and chosen.is_video:
        rel = f"images/{mat.image_filename(seg.index, seg.image_label)}.mp4"
        dest = lo.media_mp4(seg.index, seg.image_label)
        if await _try_fetch(user_id, episode_id, rel, dest):
            return "video"
        return None
    rel = f"images/{mat.image_filename(seg.index, seg.image_label)}.png"
    dest = lo.image_png(seg.index, seg.image_label)
    if await _try_fetch(user_id, episode_id, rel, dest):
        return "image"
    return None


async def reuse_voice(
    user_id: str, episode_id: str, lo: ProjectLayout, *, want_hash: str, prev: dict
) -> bool:
    """Reuse the cached ``voice/voice.mp3`` when present + unchanged. True on hit."""
    if (prev or {}).get("voice") != want_hash:
        return False
    return await _try_fetch(user_id, episode_id, "voice/voice.mp3", lo.voice_mp3)


async def reuse_thumbnails(
    user_id: str, episode_id: str, lo: ProjectLayout, *, want_hash: str, prev: dict
) -> bool:
    """Reuse cached thumbnails when present + unchanged. True if ≥1 was fetched.

    Thumbnails are best-effort, so a partial set still counts as a hit (the runner
    won't regenerate). Probes ``thumb_1..3.png``.
    """
    if (prev or {}).get("thumbnail") != want_hash:
        return False
    lo.thumbnails_dir.mkdir(parents=True, exist_ok=True)
    hits = 0
    from module2.thumbnail import THUMB_COUNT

    for i in range(1, THUMB_COUNT + 1):
        rel = f"thumbnails/thumb_{i}.png"
        dest = lo.thumbnails_dir / f"thumb_{i}.png"
        if await _try_fetch(user_id, episode_id, rel, dest):
            hits += 1
    return hits > 0


async def reuse_final(user_id: str, episode_id: str, lo: ProjectLayout) -> bool:
    """Fetch a cached ``final.mp4`` into the work folder if present. True on hit.

    Unlike the other assets the render is cheap (CPU, no API spend), so the runner
    only reuses it when EVERY input (all images + voice) was itself reused; this
    helper just makes the cached file available so the runner can re-upload it.
    """
    return await _try_fetch(user_id, episode_id, "final.mp4", lo.final_mp4)


async def reuse_srt(user_id: str, episode_id: str, lo: ProjectLayout) -> bool:
    """Fetch a cached ``subs.srt`` into the work folder if present. True on hit."""
    return await _try_fetch(user_id, episode_id, "subs.srt", lo.subs_srt)


__all__ = [
    "MANIFEST_KEY",
    "image_hash",
    "voice_hash",
    "thumbnail_hash",
    "read_manifest",
    "build_manifest",
    "reuse_segment_image",
    "reuse_voice",
    "reuse_thumbnails",
    "reuse_final",
    "reuse_srt",
]
