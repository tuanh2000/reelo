"""Wikimedia Commons image client — REAL photos as a GENERATE_IMAGE provider.

Reelo's differentiator: while the market is flooded with look-alike AI art, real
documentary/scientific/historical photos stand out. ``web-commons`` is a
first-class, **keyless** image provider (no API key to generate) that searches
Wikimedia Commons, filters to permissively-licensed raster images (Public
Domain / CC0 / CC-BY), downloads one to ``out_path``, and returns the legal
attribution in :attr:`ImageResult.raw` so the worker can persist credits.

It belongs to the ``web-*`` family of web-photo providers (future:
``web-openverse`` / ``web-pexels``) — search-based, license-filtered, attribution
carrying — as opposed to the generative providers (gemini/kie/openai/sd).

Query selection (most specific first):
1. ``ImageRequest.query`` — the model-authored ``SegmentSpec.image_query``
   (3-7 concrete English nouns), best for matching real photos.
2. de-slugified ``ImageRequest.label`` (segment ``image_label``) / out_path stem.
3. the first few words of ``prompt`` / ``prompt_file`` with style boilerplate
   stripped (least specific — generic prompts rarely match a real photo).
Each candidate is also expanded with built-in fallback variants.

Dedup: a per-episode "already used" set of Commons titles is threaded through
``ctx.extra["commons_used"]`` (a ``set[str]``) so two segments don't pick the
same photo. The client mutates that set when present.

License filter: ``extmetadata.LicenseShortName / License / UsageTerms`` must
contain a permissive token (PD / CC0 / CC-BY / "no restrictions"); CC-BY-NC,
CC-BY-ND, "fair use", etc. are rejected. Only ``image/jpeg`` and ``image/png``
are accepted (no SVG/TIFF/GIF the renderer can't Ken-Burns cleanly).
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

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# A descriptive User-Agent is REQUIRED by the Wikimedia API policy.
USER_AGENT = "ReeloVideoBot/1.0 (https://reelo.app; image-provider)"

# Permissive license tokens we accept (case-insensitive substring match).
_OK_LICENSE_TOKENS = (
    "public domain",
    "cc0",
    "cc-by",
    "cc by",
    "pdm",
    "no restrictions",
    "attribution",  # plain CC-BY UsageTerms often read "Attribution ..."
)
# Tokens that, if present, REJECT the image even if a permissive token appears
# (NonCommercial / NoDerivatives / fair use are not usable for a SaaS).
_BAD_LICENSE_TOKENS = ("noncommercial", "non-commercial", "-nc", "noderiv", "-nd", "fair use")
_OK_MIME = ("image/jpeg", "image/png")

_MIN_BYTES = 5000  # reject corrupt/placeholder thumbnails

# Aspect-ratio → requested thumbnail width (iiurlwidth). Wider for landscape.
_WIDTH_BY_ASPECT = {
    "16:9": 1600,
    "9:16": 1080,
    "1:1": 1200,
    "4:3": 1400,
    "3:4": 1080,
    "3:2": 1500,
    "2:3": 1080,
}
_DEFAULT_WIDTH = 1600

# Small preview width for the curation grid (M2-12). Keep it tiny so the
# image-candidates response is light and the grid loads fast; the large image is
# only fetched at render time for the *chosen* candidate.
_THUMB_WIDTH = 360

# Generic style boilerplate stripped from a free-form prompt before deriving a
# query from it (these never help match a real photo).
_STYLE_BOILERPLATE = re.compile(
    r"\b(oil painting|watercolou?r|illustration|digital art|concept art|render(?:ing)?|"
    r"cinematic|photorealistic|4k|8k|hyper[- ]?realistic|highly detailed|trending on \w+|"
    r"masterpiece|artstation|studio lighting|bokeh|depth of field|wide angle|close[- ]?up)\b",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^a-z0-9]+")


def _deslug(text: str) -> str:
    """Turn a slug / file stem (``red-knot-bird``, ``06_red_knot``) into words."""
    # Drop a leading zero-padded index prefix like "06_".
    text = re.sub(r"^\d+[_-]", "", text or "")
    words = [w for w in _NON_WORD.sub(" ", text.lower()).split() if w]
    return " ".join(words)


def _prompt_keywords(prompt: str, *, max_words: int = 6) -> str:
    """First few meaningful words of a prompt, with style boilerplate stripped."""
    cleaned = _STYLE_BOILERPLATE.sub(" ", prompt or "")
    # Use only the first sentence/clause — later clauses are usually style.
    first = re.split(r"[.;\n]", cleaned, maxsplit=1)[0]
    words = [w for w in _NON_WORD.sub(" ", first.lower()).split() if len(w) > 2]
    return " ".join(words[:max_words])


def _broaden(query: str, *, ns: tuple[int, ...] = (4, 3, 2)) -> list[str]:
    """Progressively shorter prefixes of ``query`` (drop trailing words).

    A too-specific multi-word query that returns nothing on Commons often matches
    after dropping the long tail; used to broaden candidate search.
    """
    words = (query or "").split()
    out: list[str] = []
    for n in ns:
        if len(words) > n:
            cand = " ".join(words[:n])
            if cand not in out:
                out.append(cand)
    return out


def _license_ok(extmeta: dict[str, Any]) -> bool:
    """True iff the file is permissively licensed (PD/CC0/CC-BY) and not NC/ND."""
    blob = " ".join(
        str((extmeta.get(key) or {}).get("value", "")).lower()
        for key in ("LicenseShortName", "License", "UsageTerms")
    )
    if not blob.strip():
        return False
    if any(bad in blob for bad in _BAD_LICENSE_TOKENS):
        return False
    return any(tok in blob for tok in _OK_LICENSE_TOKENS)


def _attribution(title: str, extmeta: dict[str, Any], info: dict[str, Any]) -> dict[str, str]:
    """Build the attribution block stored in :attr:`ImageResult.raw`."""

    def _val(key: str) -> str:
        return str((extmeta.get(key) or {}).get("value", "")).strip()

    # Artist field is HTML; strip tags for a plain credit string.
    artist = re.sub(r"<[^>]+>", "", _val("Artist")).strip()
    return {
        "title": title,
        "author": artist or "Unknown",
        "license": _val("LicenseShortName") or _val("License") or "see source",
        "source_url": info.get("descriptionurl") or info.get("url") or "",
        "descriptionurl": info.get("descriptionurl") or "",
    }


# Matches the width segment of a Commons thumbnail URL, e.g.
# ".../thumb/a/ab/Foo.jpg/360px-Foo.jpg" — used to re-size a thumb to another
# width without a second API round-trip.
_THUMB_WIDTH_RE = re.compile(r"/(\d+)px-")


def _resize_thumb_url(thumb_url: str, new_width: int) -> str:
    """Rewrite the embedded ``NNNpx-`` width of a Commons thumb URL.

    Commons serves any width off the same ``/thumb/`` path, so given a sized
    thumburl we can cheaply derive a larger/smaller one (no extra API call).
    Falls back to the original url if the pattern is absent (full-res ``url``).
    """
    if not thumb_url:
        return thumb_url
    new_seg = f"/{new_width}px-"
    rewritten, n = _THUMB_WIDTH_RE.subn(new_seg, thumb_url, count=1)
    return rewritten if n else thumb_url


class CommonsImageClient(AIClient):
    """Real-photo GENERATE_IMAGE provider backed by Wikimedia Commons (keyless)."""

    capabilities = {Task.GENERATE_IMAGE}
    cost_tier = "free"
    requires_key = False
    provider_id = "web-commons"
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

    def _search_width(self, size: str) -> int:
        return _WIDTH_BY_ASPECT.get(size, _DEFAULT_WIDTH)

    def _search_limit(self) -> int:
        return int(self._image_block().get("search_limit", 15))

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

        # 1. model-authored query (best).
        _add(req.query)
        # 2. de-sluggified label / out_path stem.
        _add(_deslug(req.label or ""))
        _add(_deslug(out_path.stem))
        # 3. prompt keywords (least specific).
        prompt = req.prompt
        if prompt is None and req.prompt_file is not None:
            try:
                prompt = Path(req.prompt_file).read_text(encoding="utf-8")
            except OSError:
                prompt = None
        if prompt:
            _add(_prompt_keywords(prompt))

        # Built-in fallbacks: progressively drop the trailing word so a 6-word
        # query that returns nothing still has shorter, broader retries.
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
        self, client: httpx.AsyncClient, query: str, width: int
    ) -> list[dict[str, Any]]:
        params = {
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": "6",  # File: namespace
            "gsrlimit": str(self._search_limit()),
            "prop": "imageinfo",
            "iiprop": "url|extmetadata|mime",
            "iiurlwidth": str(width),
        }
        last_exc: Exception | None = None
        for attempt in range(self._retries() + 1):
            try:
                resp = await client.get(COMMONS_API, params=params, timeout=self._timeout())
                resp.raise_for_status()
                pages = ((resp.json().get("query") or {}).get("pages")) or {}
                # Honour the search relevance order (lower index = better match).
                return sorted(pages.values(), key=lambda p: p.get("index", 9999))
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                if attempt < self._retries():
                    await asyncio.sleep(0.4 * (attempt + 1))
        if last_exc is not None:
            # A single failing query should not abort the whole search; the
            # caller tries the next candidate. Surface only via empty result.
            return []
        return []

    async def _download(
        self, client: httpx.AsyncClient, url: str, dest: Path
    ) -> bool:
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

    # ---- page → candidate (license/mime filter applied) --------------------
    def _page_to_candidate(self, page: dict[str, Any], full_width: int) -> ImageCandidate | None:
        """Build an :class:`ImageCandidate` from a search page, or None if rejected.

        Rejects non-raster mimes and non-permissive licenses (same rules as the
        auto path) so the curation grid only shows legally-usable photos.
        """
        title = page.get("title", "")
        info = (page.get("imageinfo") or [{}])[0]
        mime = info.get("mime", "")
        extmeta = info.get("extmetadata", {}) or {}
        thumb = info.get("thumburl") or info.get("url")
        if not title or mime not in _OK_MIME or not thumb:
            return None
        if not _license_ok(extmeta):
            return None
        attribution = _attribution(title, extmeta, info)
        # thumburl was sized to _THUMB_WIDTH at search time; derive a large URL by
        # rewriting the width (no extra API round-trip). Fall back to the original.
        full_url = _resize_thumb_url(thumb, full_width)
        return ImageCandidate(
            id=title,
            thumb_url=thumb,
            full_url=full_url,
            title=attribution["title"],
            author=attribution["author"],
            license=attribution["license"],
            source_url=attribution["source_url"],
            descriptionurl=attribution["descriptionurl"],
            width=int(info.get("thumbwidth", 0) or 0),
            height=int(info.get("thumbheight", 0) or 0),
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
        """Search Commons; return up to ``limit`` license-clean photo candidates.

        Downloads NOTHING — only metadata + a small (~360px) ``thumb_url`` preview
        per hit (the grid stays light). The large image is fetched later, for the
        *chosen* candidate only, via :meth:`download_chosen`. ``exclude`` drops
        already-used titles so two segments don't surface the same photo first.
        """
        exclude = exclude or set()
        full_width = self._search_width(size)
        candidates: list[ImageCandidate] = []
        seen: set[str] = set()

        # Try the query, then progressively broadened variants (drop trailing
        # words) so a too-specific 5-word query that returns nothing still fills
        # the grid — same robustness the auto path gets from _candidate_queries.
        queries: list[str] = []
        for q in [query, *_broaden(query)]:
            q = (q or "").strip()
            if q and q.lower() not in {x.lower() for x in queries}:
                queries.append(q)

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for q in queries:
                # The candidate grid wants the *small* thumb width, so search at it.
                pages = await self._search_page(client, q, _THUMB_WIDTH)
                for page in pages:
                    cand = self._page_to_candidate(page, full_width)
                    if cand is None or cand.id in exclude or cand.id in seen:
                        continue
                    seen.add(cand.id)
                    candidates.append(cand)
                    if len(candidates) >= limit:
                        return candidates
                if candidates:
                    # Got something from this query; don't dilute with broader,
                    # less-relevant hits unless we still have room AND nothing yet.
                    break
        return candidates

    async def download_chosen(
        self, candidate: ImageCandidate, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        """Download a chosen candidate's large image to ``out_path`` (+ attribution).

        Reuses the same downloader/retry as the auto path; records usage. Raises
        :class:`ProviderUnavailableError` if neither the large nor preview URL can
        be fetched (caller may then fall back to the auto path).
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
                    f"web-commons: could not download chosen image {candidate.id!r}"
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

    # ---- generate-image ----------------------------------------------------
    async def generate_image(
        self, req: ImageRequest, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        queries = self._candidate_queries(req, out_path)
        if not queries:
            raise ProviderUnavailableError(
                "web-commons: no query could be derived (no image_query, label, or prompt)"
            )

        used: set[str] = ctx.extra.get("commons_used") if isinstance(ctx.extra, dict) else None  # type: ignore[assignment]
        if used is None:
            used = set()
        width = self._search_width(req.size)

        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            for query in queries:
                pages = await self._search_page(client, query, width)
                for page in pages:
                    title = page.get("title", "")
                    info = (page.get("imageinfo") or [{}])[0]
                    mime = info.get("mime", "")
                    extmeta = info.get("extmetadata", {}) or {}
                    thumb = info.get("thumburl") or info.get("url")
                    if not title or title in used or mime not in _OK_MIME or not thumb:
                        continue
                    if not _license_ok(extmeta):
                        continue
                    # Write to the EXACT out_path the caller asked for, keeping
                    # its extension (the materializer/renderer track images by a
                    # fixed ``NN_<label>.png`` path). The real mime is jpeg/png;
                    # ffmpeg detects format from content, not the filename, so a
                    # ``.png`` file holding JPEG bytes renders fine. The true mime
                    # is recorded in ``raw`` for downstream/debugging.
                    dest = out_path
                    if not await self._download(client, thumb, dest):
                        continue
                    used.add(title)
                    # Keep the shared set discoverable for the next segment.
                    if isinstance(ctx.extra, dict):
                        ctx.extra["commons_used"] = used

                    attribution = _attribution(title, extmeta, info)
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
                            "mime": mime,
                            "thumburl": thumb,
                            "attribution": attribution,
                        },
                    )

        raise ProviderUnavailableError(
            "web-commons: no permissively-licensed (PD/CC0/CC-BY) raster image found for "
            f"queries {queries!r}"
        )


__all__ = ["CommonsImageClient", "USER_AGENT", "COMMONS_API"]
