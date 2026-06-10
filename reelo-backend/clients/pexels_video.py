"""Pexels Video client — REAL stock video CLIPS as a media provider (M2-13).

Reelo's curate grid is not images-only: each segment can use a real video clip
from the web instead of a still photo, mixed freely with Commons photos. This is
the ``web-pexels`` provider — a member of the ``web-*`` family alongside
``web-commons``, but it returns **video** candidates (``MediaCandidate`` with
``media_type="video"``) from the Pexels Video Search API.

Unlike keyless ``web-commons``, Pexels requires a free BYOK API key (key_ref
``pexels``, env ``PEXELS_API_KEY``). When the user has no Pexels key the curate
grid simply has no clips (graceful: only Commons photos show). All Pexels content
is free to use under the Pexels License (CC0-like; attribution appreciated, not
required) — usable by a SaaS the same way Commons PD/CC0/CC-BY is.

Search returns metadata + a poster image only (``poster_url``); the mp4 is
downloaded **only for the chosen clip** at render time via
:meth:`download_chosen`. The renderer (``module2.render.build_video_clip_cmd``)
then scale/crops it to the frame, trims/loops it to the segment duration, mutes
the source audio, and normalizes fps — see Module 2 §4.3 / M2-13.

File selection: from a video's ``video_files`` we pick the mp4 whose dimensions
are closest to the target frame for the series aspect (e.g. ~1920×1080 for 16:9),
preferring HD. That keeps download bandwidth proportional to the frame instead of
always pulling 4K.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from clients.base import (
    AIClient,
    CallContext,
    ImageResult,
    InvalidKeyError,
    MediaCandidate,
    ProviderUnavailableError,
    Task,
)
from usage import compute_cost

PEXELS_VIDEO_SEARCH = "https://api.pexels.com/videos/search"

# Target frame (long/short edge) per series aspect — used to pick the mp4 whose
# size is closest, and to map the aspect onto Pexels' orientation/size hints.
_FRAME_BY_ASPECT: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "3:2": (1620, 1080),
    "2:3": (1080, 1620),
}
_DEFAULT_FRAME = (1920, 1080)

# Pexels License is permissive (CC0-like): free for commercial use, no attribution
# required (but we still record the videographer for a nice credit).
_PEXELS_LICENSE = "Pexels License (free, CC0-like)"

_MIN_BYTES = 10000  # reject truncated downloads


def _orientation_for(aspect: str) -> str:
    """Map a series aspect onto a Pexels ``orientation`` hint."""
    fw, fh = _FRAME_BY_ASPECT.get(aspect, _DEFAULT_FRAME)
    if fw > fh:
        return "landscape"
    if fh > fw:
        return "portrait"
    return "square"


def _pick_video_file(
    video_files: list[dict[str, Any]], target: tuple[int, int]
) -> dict[str, Any] | None:
    """Pick the mp4 file whose dimensions are closest to the target frame.

    Only ``video/mp4`` files are considered (the renderer expects mp4). Among
    those, the one minimizing |area - target_area| wins; ties prefer the larger
    file so we don't pick a postage-stamp. Returns the chosen file dict or None.
    """
    tw, th = target
    target_area = tw * th
    best: dict[str, Any] | None = None
    best_key: tuple[float, int] | None = None
    for f in video_files:
        if (f.get("file_type") or "").lower() != "video/mp4":
            continue
        if not f.get("link"):
            continue
        w = int(f.get("width") or 0)
        h = int(f.get("height") or 0)
        area = w * h
        # primary: distance to target area; secondary: prefer larger on a tie.
        key = (abs(area - target_area), -area)
        if best_key is None or key < best_key:
            best_key = key
            best = f
    return best


class PexelsVideoClient(AIClient):
    """Real stock-video CLIP provider backed by Pexels (BYOK, free)."""

    capabilities = {Task.GENERATE_IMAGE}  # shares the media task; media_type="video"
    cost_tier = "free"
    requires_key = True
    provider_id = "web-pexels"
    # web-* providers offer a per-segment human-curation candidate list (M2-12/13).
    supports_candidates = True

    # ---- config helpers ----------------------------------------------------
    def _key(self, ctx: CallContext) -> str | None:
        key_ref = self.config.auth.key_ref or "pexels"
        return ctx.keys.get(ctx.user_id, key_ref)

    def _timeout(self) -> float:
        return float(self.config.raw.get("timeout", 30.0))

    def _retries(self) -> int:
        return int(self.config.raw.get("retries", 2))

    def _search_limit(self) -> int:
        return int(
            (self.config.tasks.get(Task.GENERATE_IMAGE.value, {}) or {}).get("search_limit", 15)
        )

    def _target_frame(self, size: str) -> tuple[int, int]:
        return _FRAME_BY_ASPECT.get(size, _DEFAULT_FRAME)

    def _pexels_size(self, aspect: str) -> str:
        """Map the frame to a Pexels ``size`` bucket (small=HD, medium=FHD)."""
        fw, fh = self._target_frame(aspect)
        long_edge = max(fw, fh)
        # HD (≈720) is plenty for ≤1080 frames; medium (FHD) for exactly 1080.
        return "medium" if long_edge >= 1920 else "small"

    # ---- HTTP --------------------------------------------------------------
    async def _search(
        self, client: httpx.AsyncClient, query: str, size: str
    ) -> list[dict[str, Any]]:
        params = {
            "query": query,
            "per_page": str(self._search_limit()),
            "orientation": _orientation_for(size),
            "size": self._pexels_size(size),
        }
        last_exc: Exception | None = None
        for attempt in range(self._retries() + 1):
            try:
                resp = await client.get(
                    PEXELS_VIDEO_SEARCH, params=params, timeout=self._timeout()
                )
                if resp.status_code in (401, 403):
                    raise InvalidKeyError("web-pexels: invalid or unauthorized Pexels API key")
                resp.raise_for_status()
                return list((resp.json() or {}).get("videos") or [])
            except InvalidKeyError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if attempt < self._retries():
                    await asyncio.sleep(0.4 * (attempt + 1))
        if last_exc is not None:
            return []
        return []

    async def _download(self, client: httpx.AsyncClient, url: str, dest: Path) -> bool:
        for attempt in range(self._retries() + 1):
            try:
                resp = await client.get(url, timeout=self._timeout())
                resp.raise_for_status()
                dest.write_bytes(resp.content)
                if dest.stat().st_size >= _MIN_BYTES:
                    return True
                dest.unlink(missing_ok=True)
                return False
            except httpx.HTTPError:
                if attempt < self._retries():
                    await asyncio.sleep(0.4 * (attempt + 1))
        return False

    # ---- video → candidate -------------------------------------------------
    def _video_to_candidate(
        self, video: dict[str, Any], target: tuple[int, int]
    ) -> MediaCandidate | None:
        """Build a video :class:`MediaCandidate`, choosing the closest-fit mp4."""
        chosen = _pick_video_file(video.get("video_files") or [], target)
        if chosen is None:
            return None
        vid = video.get("id")
        if vid is None:
            return None
        cand_id = f"pexels-{vid}"
        poster = str(video.get("image") or "")
        author = str(((video.get("user") or {}).get("name")) or "Pexels")
        page = str(video.get("url") or "")
        # Optional short preview clip for hover-play: a small video_file if any.
        preview = ""
        small = _pick_video_file(video.get("video_files") or [], (640, 360))
        if small is not None:
            preview = str(small.get("link") or "")
        return MediaCandidate(
            id=cand_id,
            thumb_url=poster,
            full_url=str(chosen.get("link") or ""),
            title=f"Pexels video {vid}",
            author=author,
            license=_PEXELS_LICENSE,
            source_url=page,
            descriptionurl=page,
            width=int(chosen.get("width") or 0),
            height=int(chosen.get("height") or 0),
            media_type="video",
            duration=float(video.get("duration") or 0.0),
            poster_url=poster,
            preview_url=preview,
            video_url=str(chosen.get("link") or ""),
        )

    # ---- candidate curation (M2-13) ----------------------------------------
    async def search_candidates(
        self,
        query: str,
        ctx: CallContext,
        *,
        size: str = "16:9",
        limit: int = 9,
        exclude: set[str] | None = None,
    ) -> list[MediaCandidate]:
        """Search Pexels Video; return up to ``limit`` clip candidates (metadata only).

        Downloads NOTHING — only a poster image url per hit. The mp4 is fetched
        later, for the *chosen* candidate only, via :meth:`download_chosen`. No key
        → empty list (graceful: the merged grid then has only Commons photos).
        """
        key = self._key(ctx)
        if not key:
            return []
        exclude = exclude or set()
        target = self._target_frame(size)
        out: list[MediaCandidate] = []
        seen: set[str] = set()
        async with httpx.AsyncClient(
            headers={"Authorization": key}, follow_redirects=True
        ) as client:
            videos = await self._search(client, query, size)
            for v in videos:
                cand = self._video_to_candidate(v, target)
                if cand is None or cand.id in exclude or cand.id in seen:
                    continue
                seen.add(cand.id)
                out.append(cand)
                if len(out) >= limit:
                    break
        return out

    async def download_chosen(
        self, candidate: MediaCandidate, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        """Download the chosen clip's mp4 to ``out_path`` (+ attribution).

        Raises :class:`ProviderUnavailableError` if the file cannot be fetched (the
        runner may then fall back to the auto image path).
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        key = self._key(ctx)
        if not key:
            raise ProviderUnavailableError("web-pexels: no Pexels API key for this user")
        url = candidate.video_url or candidate.full_url
        async with httpx.AsyncClient(
            headers={"Authorization": key}, follow_redirects=True
        ) as client:
            if not url or not await self._download(client, url, out_path):
                raise ProviderUnavailableError(
                    f"web-pexels: could not download chosen clip {candidate.id!r}"
                )
        cost = compute_cost(Task.GENERATE_IMAGE.value, 1.0, self.config.pricing)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_IMAGE.value, 1.0, cost)
        return ImageResult(
            out_path=out_path,
            count=1,
            raw={
                "provider": self.provider_id,
                "chosen_id": candidate.id,
                "media_type": "video",
                "duration": candidate.duration,
                "attribution": candidate.attribution(),
            },
        )

    # ---- key validation (M3-5) --------------------------------------------
    async def validate_key(self, ctx: CallContext) -> bool:
        """One light search (``nature``, 1 result) to confirm the key works."""
        key = self._key(ctx)
        if not key:
            return False
        async with httpx.AsyncClient(
            headers={"Authorization": key}, follow_redirects=True
        ) as client:
            try:
                resp = await client.get(
                    PEXELS_VIDEO_SEARCH,
                    params={"query": "nature", "per_page": "1"},
                    timeout=self._timeout(),
                )
            except httpx.HTTPError:
                return False
            if resp.status_code in (401, 403):
                return False
            return resp.status_code == 200


__all__ = ["PexelsVideoClient", "PEXELS_VIDEO_SEARCH"]
