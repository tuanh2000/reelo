"""Openverse image client — REAL photos as a GENERATE_IMAGE provider (keyless).

A member of the ``web-*`` family alongside ``web-commons`` (Wikimedia photos) and
``web-pexels`` (video clips). ``web-openverse`` searches **Openverse**
(``api.openverse.org``) — an aggregator of ~800M openly-licensed images
(CC / Public Domain) across many sources (Flickr, museums, Wikimedia, …). Like
``web-commons`` it is **keyless** (anonymous access is rate-limited but needs no
API key), returns real photos (not AI art), and downloads fast.

Same shape as :mod:`clients.commons_image`:
- ``capabilities={GENERATE_IMAGE}``, ``requires_key=False``, ``cost_tier="free"``,
  ``provider_id="web-openverse"``, ``supports_candidates=True``.
- :meth:`search_candidates` searches Openverse, returns a list of
  :class:`MediaCandidate` (``media_type="image"``, ``thumb_url`` preview,
  ``full_url`` original, ``title``/``author``/``license`` attribution) for the
  human-curation grid (M2-12) — downloads NOTHING.
- :meth:`download_chosen` fetches the chosen candidate's original image to
  ``out_path`` and records usage + attribution in :attr:`ImageResult.raw`.
- :meth:`generate_image` (the auto path) searches + downloads the first
  license-clean hit straight to ``out_path``, mirroring the Commons auto path
  (query priority image_query → de-slugged label → prompt keywords; per-episode
  dedup via ``ctx.extra["openverse_used"]``).

Openverse API (free, anonymous):
- Search: ``GET https://api.openverse.org/v1/images/`` with ``q``, ``page_size``,
  and a permissive ``license_type=commercial`` filter (CC0/PDM/BY/BY-SA — safe for
  a SaaS to reuse; excludes NC/ND). Response: ``results[]`` each with ``id``,
  ``title``, ``url`` (original), ``thumbnail``, ``creator``, ``license``,
  ``license_url``, ``foreign_landing_url``, ``source``.
- Download: ``GET`` the ``url`` (or ``thumbnail`` fallback) → bytes, with a polite
  ``User-Agent``.

A descriptive User-Agent is good etiquette (and softens anonymous rate-limiting).
HTTP errors degrade to an empty result (candidate search) or
:class:`ProviderUnavailableError` (auto path) — never a crash.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import httpx

from clients.base import (
    AIClient,
    CallContext,
    ImageCandidate,
    ImageRequest,
    ImageResult,
    ProviderUnavailableError,
    Task,
)
from usage import compute_cost

OPENVERSE_IMAGE_SEARCH = "https://api.openverse.org/v1/images/"
# A descriptive User-Agent is polite (and eases anonymous rate-limiting).
USER_AGENT = "ReeloVideoBot/1.0 (https://reelo.app; image-provider)"

# License *types* we ask Openverse to return. ``commercial`` covers CC0 / PDM /
# CC-BY / CC-BY-SA and excludes the NonCommercial / NoDerivatives variants a SaaS
# cannot reuse. (Openverse also supports an explicit ``license=cc0,pdm,by,by-sa``
# filter; the type filter is broader and simpler.)
_LICENSE_TYPE = "commercial"

# Defence-in-depth: even with the license_type filter, reject any result whose
# license string carries an NC/ND token (in case the upstream metadata is loose).
_BAD_LICENSE_TOKENS = ("nc", "nd")

_MIN_BYTES = 5000  # reject corrupt/placeholder thumbnails

# Generic style boilerplate stripped from a free-form prompt before deriving a
# query from it (these never help match a real photo). Mirrors web-commons.
_STYLE_BOILERPLATE = re.compile(
    r"\b(oil painting|watercolou?r|illustration|digital art|concept art|render(?:ing)?|"
    r"cinematic|photorealistic|4k|8k|hyper[- ]?realistic|highly detailed|trending on \w+|"
    r"masterpiece|artstation|studio lighting|bokeh|depth of field|wide angle|close[- ]?up)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _deslug(text: str) -> str:
    """Turn a slug / file stem (``red-knot-bird``, ``06_red_knot``) into words."""
    text = re.sub(r"^\d+[_-]", "", text or "")
    words = [w for w in _NON_WORD.sub(" ", text.lower()).split() if w]
    return " ".join(words)


def _prompt_keywords(prompt: str, *, max_words: int = 6) -> str:
    """First few meaningful words of a prompt, with style boilerplate stripped."""
    cleaned = _STYLE_BOILERPLATE.sub(" ", prompt or "")
    first = re.split(r"[.;\n]", cleaned, maxsplit=1)[0]
    words = [w for w in _NON_WORD.sub(" ", first.lower()).split() if len(w) > 2]
    return " ".join(words[:max_words])


def _broaden(query: str, *, ns: tuple[int, ...] = (4, 3, 2)) -> list[str]:
    """Progressively shorter prefixes of ``query`` (drop trailing words)."""
    words = (query or "").split()
    out: list[str] = []
    for n in ns:
        if len(words) > n:
            cand = " ".join(words[:n])
            if cand not in out:
                out.append(cand)
    return out


def _license_ok(result: dict[str, Any]) -> bool:
    """True iff the result's license is permissive (no NC/ND token).

    The ``license_type=commercial`` query already excludes NC/ND, so this is a
    belt-and-braces check against loose upstream metadata.
    """
    lic = str(result.get("license") or "").strip().lower()
    if not lic:
        # No license string but the API returned it under the commercial filter;
        # treat as acceptable (the server-side filter is authoritative).
        return True
    parts = set(re.split(r"[-_\s]+", lic))
    return not any(bad in parts for bad in _BAD_LICENSE_TOKENS)


def _attribution(result: dict[str, Any]) -> dict[str, str]:
    """Build the attribution block stored in :attr:`ImageResult.raw`."""
    landing = str(result.get("foreign_landing_url") or result.get("url") or "")
    return {
        "title": str(result.get("title") or "Untitled"),
        "author": str(result.get("creator") or "Unknown"),
        "license": str(result.get("license") or "see source"),
        "source_url": landing,
        "descriptionurl": landing,
    }


class OpenverseImageClient(AIClient):
    """Real-photo GENERATE_IMAGE provider backed by Openverse (keyless)."""

    capabilities = {Task.GENERATE_IMAGE}
    cost_tier = "free"
    requires_key = False
    provider_id = "web-openverse"
    # web-* photo providers offer a human-curation candidate list (M2-12).
    supports_candidates = True

    # ---- availability (keyless; never block on a network ping) -------------
    async def is_available(self, ctx: CallContext) -> bool:
        return True

    async def validate_key(self, ctx: CallContext) -> bool:
        """Keyless: nothing to validate."""
        return True

    # ---- config helpers ----------------------------------------------------
    def _image_block(self) -> dict[str, Any]:
        return self.config.tasks.get(Task.GENERATE_IMAGE.value, {}) or {}

    def _page_size(self) -> int:
        return int(self._image_block().get("search_limit", 9))

    def _timeout(self) -> float:
        return float(self.config.raw.get("timeout", 30.0))

    def _retries(self) -> int:
        return int(self.config.raw.get("retries", 2))

    # ---- query building ----------------------------------------------------
    def _candidate_queries(self, req: ImageRequest, out_path: Path) -> list[str]:
        """Ordered, de-duplicated list of search queries (most specific first)."""
        ordered: list[str] = []

        def _add(q: str | None) -> None:
            q = (q or "").strip()
            if q and q.lower() not in {x.lower() for x in ordered}:
                ordered.append(q)

        _add(req.query)
        _add(_deslug(req.label or ""))
        _add(_deslug(out_path.stem))
        prompt = req.prompt
        if prompt is None and req.prompt_file is not None:
            try:
                prompt = Path(req.prompt_file).read_text(encoding="utf-8")
            except OSError:
                prompt = None
        if prompt:
            _add(_prompt_keywords(prompt))

        broadened: list[str] = []
        for q in list(ordered):
            words = q.split()
            for n in (4, 3, 2):
                if len(words) > n:
                    cand = " ".join(words[:n])
                    if cand.lower() not in {x.lower() for x in ordered + broadened}:
                        broadened.append(cand)
        ordered.extend(broadened)
        return ordered

    # ---- HTTP search + download (with light retry) -------------------------
    async def _search_page(
        self, client: httpx.AsyncClient, query: str, page_size: int
    ) -> list[dict[str, Any]]:
        params = {
            "q": query,
            "page_size": str(page_size),
            "license_type": _LICENSE_TYPE,
        }
        last_exc: Exception | None = None
        for attempt in range(self._retries() + 1):
            try:
                resp = await client.get(
                    OPENVERSE_IMAGE_SEARCH, params=params, timeout=self._timeout()
                )
                resp.raise_for_status()
                return list((resp.json() or {}).get("results") or [])
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if attempt < self._retries():
                    await asyncio.sleep(0.4 * (attempt + 1))
        if last_exc is not None:
            # A single failing query should not abort the whole search; the caller
            # tries the next candidate. Surface only via an empty result.
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

    # ---- result → candidate (license filter applied) ----------------------
    def _result_to_candidate(self, result: dict[str, Any]) -> ImageCandidate | None:
        """Build an :class:`ImageCandidate` from a search result, or None if rejected."""
        rid = result.get("id")
        thumb = result.get("thumbnail") or result.get("url")
        full = result.get("url") or result.get("thumbnail")
        if not rid or not thumb or not full:
            return None
        if not _license_ok(result):
            return None
        attribution = _attribution(result)
        return ImageCandidate(
            id=str(rid),
            thumb_url=str(thumb),
            full_url=str(full),
            title=attribution["title"],
            author=attribution["author"],
            license=attribution["license"],
            source_url=attribution["source_url"],
            descriptionurl=attribution["descriptionurl"],
            width=int(result.get("width", 0) or 0),
            height=int(result.get("height", 0) or 0),
        )

    # ---- candidate curation (M2-12) ----------------------------------------
    async def search_candidates(
        self,
        query: str,
        ctx: CallContext,
        *,
        size: str = "16:9",
        limit: int = 9,
        exclude: set[str] | None = None,
    ) -> list[ImageCandidate]:
        """Search Openverse; return up to ``limit`` license-clean photo candidates.

        Downloads NOTHING — only metadata + a ``thumb_url`` preview per hit (the
        grid stays light). The original image is fetched later, for the *chosen*
        candidate only, via :meth:`download_chosen`. ``exclude`` drops already-used
        ids so two segments don't surface the same photo first.
        """
        exclude = exclude or set()
        candidates: list[ImageCandidate] = []
        seen: set[str] = set()

        queries: list[str] = []
        for q in [query, *_broaden(query)]:
            q = (q or "").strip()
            if q and q.lower() not in {x.lower() for x in queries}:
                queries.append(q)

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for q in queries:
                results = await self._search_page(client, q, max(limit, self._page_size()))
                for result in results:
                    cand = self._result_to_candidate(result)
                    if cand is None or cand.id in exclude or cand.id in seen:
                        continue
                    seen.add(cand.id)
                    candidates.append(cand)
                    if len(candidates) >= limit:
                        return candidates
                if candidates:
                    # Got hits from this query; don't dilute with broader, less
                    # relevant ones unless we still have room AND nothing yet.
                    break
        return candidates

    async def download_chosen(
        self, candidate: ImageCandidate, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        """Download a chosen candidate's original image to ``out_path`` (+ attribution).

        Tries the original ``full_url`` then the ``thumb_url`` preview. Raises
        :class:`ProviderUnavailableError` if neither can be fetched (the runner may
        then fall back to the auto path).
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            ok = False
            for url in (candidate.full_url, candidate.thumb_url):
                if url and await self._download(client, url, out_path):
                    ok = True
                    break
            if not ok:
                raise ProviderUnavailableError(
                    f"web-openverse: could not download chosen image {candidate.id!r}"
                )
        cost = compute_cost(Task.GENERATE_IMAGE.value, 1.0, self.config.pricing)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_IMAGE.value, 1.0, cost)
        return ImageResult(
            out_path=out_path,
            count=1,
            raw={
                "provider": self.provider_id,
                "chosen_id": candidate.id,
                "attribution": candidate.attribution(),
            },
        )

    # ---- generate-image (auto path) ---------------------------------------
    async def generate_image(
        self, req: ImageRequest, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        queries = self._candidate_queries(req, out_path)
        if not queries:
            raise ProviderUnavailableError(
                "web-openverse: no query could be derived (no image_query, label, or prompt)"
            )

        used: set[str] = ctx.extra.get("openverse_used") if isinstance(ctx.extra, dict) else None  # type: ignore[assignment]
        if used is None:
            used = set()
        page_size = self._page_size()

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for query in queries:
                results = await self._search_page(client, query, page_size)
                for result in results:
                    rid = result.get("id")
                    url = result.get("url") or result.get("thumbnail")
                    if not rid or str(rid) in used or not url:
                        continue
                    if not _license_ok(result):
                        continue
                    # Write to the EXACT out_path the caller asked for (the
                    # materializer/renderer track images by a fixed
                    # ``NN_<label>.png`` path). ffmpeg detects the real format from
                    # content, not the filename, so a ``.png`` holding JPEG bytes
                    # renders fine.
                    dest = out_path
                    if not await self._download(client, str(url), dest):
                        continue
                    used.add(str(rid))
                    if isinstance(ctx.extra, dict):
                        ctx.extra["openverse_used"] = used

                    attribution = _attribution(result)
                    cost = compute_cost(Task.GENERATE_IMAGE.value, 1.0, self.config.pricing)
                    ctx.usage.record(
                        ctx.user_id, self.provider_id, Task.GENERATE_IMAGE.value, 1.0, cost
                    )
                    return ImageResult(
                        out_path=dest,
                        count=1,
                        raw={
                            "provider": self.provider_id,
                            "query": query,
                            "url": str(url),
                            "attribution": attribution,
                        },
                    )

        raise ProviderUnavailableError(
            "web-openverse: no permissively-licensed (CC/PD) image found for "
            f"queries {queries!r}"
        )


__all__ = ["OpenverseImageClient", "USER_AGENT", "OPENVERSE_IMAGE_SEARCH"]
