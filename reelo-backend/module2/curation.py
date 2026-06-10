"""Media curation (M2-12 / M2-13) — per-segment candidate state for web media.

The differentiator: for web-* providers the system does NOT auto-pick one media
per segment. Instead it offers a grid of license-clean candidates per segment so a
human curates the on-topic media. Since M2-13 each segment's grid **mixes** real
photos (``web-commons``, keyless) with real video clips (``web-pexels``, BYOK) so
a segment can be a still image OR a video clip. This module is the pure logic the
endpoints and the Module 2 runner share:

- :func:`web_media_providers` — the web-* providers (``supports_candidates``) the
  current user may use right now (keyless always; keyed only with a key present).
- :func:`provider_supports_candidates` — does the episode's image provider have a
  curation step (a single web-* provider, or the ``web`` aggregate).
- :func:`build_curation` — search candidates per segment across the selected
  provider(s) and **merge** them (downloads nothing); seed ``chosen_id`` with the
  first candidate. Returns the JSONB blob stored in ``Episode.image_curation``.
- :func:`apply_selection` — validate + apply ``{segment_index: candidate_id}``.
- :func:`chosen_candidate_for` — read the chosen :class:`MediaCandidate` for a
  segment, and :func:`chosen_provider_for` — which web-* provider it came from
  (so the runner downloads it with the right client at render time).

Persistence shape (``Episode.image_curation``)::

    {"provider": "web",            # "web-commons" | "web-pexels" | "web" (mixed)
     "segments": [
        {"index": 1, "query": "...", "text": "...",
         "candidates": [
            {<MediaCandidate.to_dict()>, "provider": "web-commons"},   # image
            {<MediaCandidate.to_dict()>, "provider": "web-pexels"},    # video
            ...],
         "chosen_id": "File:Foo.jpg"},
        ...]}

It is pre-produce data kept OUT of the canonical ``SeriesSpec`` (decision M2-12)
— curation lives on the Episode row at status ``scripted``; ``produce`` consumes
the choices.
"""

from __future__ import annotations

from typing import Any

from clients.base import AIClient, CallContext, MediaCandidate, Task
from clients.registry import ServiceRegistry
from models.spec import EpisodeSpec
from module2.materialize import deslug

CANDIDATES_PER_SEGMENT = 9
# Per-source cap when merging (so neither photos nor clips crowd out the other).
PHOTOS_PER_SEGMENT = 6
CLIPS_PER_SEGMENT = 6

# Sentinel provider id: aggregate all available web-* media providers into one
# mixed grid (photos + clips). Users pick it in Setup as ``providers.image="web"``.
WEB_AGGREGATE = "web"


def image_provider_id(series_providers: dict[str, str]) -> str:
    """The configured image provider id (defaults to ``kie`` like the runner)."""
    return series_providers.get("image", "kie")


def _is_web_media_provider(client: AIClient | None) -> bool:
    return bool(client is not None and getattr(client, "supports_candidates", False))


async def web_media_providers(
    registry: ServiceRegistry, provider_id: str, ctx: CallContext
) -> list[AIClient]:
    """The web-* media clients to query for ``provider_id``, available to the user.

    - ``provider_id == "web"`` → every web-* provider (``supports_candidates``)
      that ``is_available`` for this user (keyless always; BYOK only with a key).
    - a specific ``web-*`` provider → just that one if available.
    - anything else (generative / unknown) → ``[]`` (no curation step).

    Ordered so keyless photo providers (``web-commons``) come first, giving a
    stable, image-first default selection in the merged grid.
    """
    if provider_id == WEB_AGGREGATE:
        clients = [
            c
            for c in registry.for_capability(Task.GENERATE_IMAGE)
            if _is_web_media_provider(c)
        ]
    else:
        c = registry.try_get(provider_id)
        clients = [c] if _is_web_media_provider(c) else []

    available: list[AIClient] = []
    for c in clients:
        if await c.is_available(ctx):
            available.append(c)
    # Keyless (web-commons photos) first, then keyed (web-pexels clips); stable.
    available.sort(key=lambda c: (getattr(c, "requires_key", True), c.provider_id))
    return available


def provider_supports_candidates(registry: ServiceRegistry, provider_id: str) -> bool:
    """True iff ``provider_id`` has a curation step (web-* single, or ``web``).

    Note: this does not require the user to have a key — the ``web`` aggregate and
    ``web-commons`` always curate (photos are keyless). ``web-pexels`` alone also
    reports True so the UI shows the selection screen; with no Pexels key the grid
    just comes back empty for that provider (graceful, handled in the endpoint).
    """
    if provider_id == WEB_AGGREGATE:
        return True
    client = registry.try_get(provider_id)
    return _is_web_media_provider(client)


def segment_query(seg: Any) -> str:
    """The search query for a segment: ``image_query`` or de-slugged label."""
    return seg.image_query or deslug(seg.image_label)


def segment_text(seg: Any, *, limit: int = 160) -> str:
    """A short narration preview for the selection grid header."""
    text = (seg.narration or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _source_cap(client: AIClient) -> int:
    """Per-source merge cap: clips vs photos (so neither dominates the grid)."""
    media_video = bool(getattr(client, "requires_key", False)) and client.provider_id == "web-pexels"
    return CLIPS_PER_SEGMENT if media_video else PHOTOS_PER_SEGMENT


async def build_curation(
    registry: ServiceRegistry,
    clients: list[AIClient] | AIClient,
    ep: EpisodeSpec,
    ctx: CallContext,
    *,
    size: str = "16:9",
    limit: int = CANDIDATES_PER_SEGMENT,
) -> dict[str, Any]:
    """Search candidates per segment across ``clients`` and **merge** them.

    ``clients`` may be a single client (legacy single-provider curation) or a list
    of web-* clients (M2-13 mixed grid). Each candidate dict records which provider
    it came from (``candidate["provider"]``) so the runner downloads it with the
    right client. Default-selects the first candidate per segment (photos first →
    an image by default). Downloads nothing (previews + metadata only). Dedups the
    *chosen* media across segments so the same top pick isn't repeated.
    """
    # Accept a single client (legacy single-provider curation) or a list. Use a
    # duck-typed check (not isinstance AIClient) so test fakes / duck clients work.
    if not isinstance(clients, (list, tuple)):
        clients = [clients]
    else:
        clients = list(clients)
    if not clients:
        # No web-* provider available to the user (e.g. only web-pexels selected
        # with no key) → empty grids; the runner falls back to the auto path.
        return {
            "provider": WEB_AGGREGATE,
            "segments": [
                {
                    "index": seg.index,
                    "query": segment_query(seg),
                    "text": segment_text(seg),
                    "candidates": [],
                    "chosen_id": None,
                }
                for seg in ep.segments
            ],
        }
    # Per-provider "already chosen" sets so each source dedups its own ids.
    used: dict[str, set[str]] = {c.provider_id: set() for c in clients}
    segments: list[dict[str, Any]] = []
    for seg in ep.segments:
        query = segment_query(seg)
        merged: list[dict[str, Any]] = []
        if query:
            for client in clients:
                cap = min(_source_cap(client), limit)
                try:
                    cands = await client.search_candidates(
                        query, ctx, size=size, limit=cap, exclude=set(used[client.provider_id])
                    )
                except Exception:  # noqa: BLE001 — one source failing must not abort
                    cands = []
                for c in cands:
                    d = c.to_dict()
                    d["provider"] = client.provider_id
                    merged.append(d)
        chosen_id = merged[0]["id"] if merged else None
        if chosen_id:
            # Mark the chosen one used for its source so it isn't the top pick twice.
            used.setdefault(merged[0]["provider"], set()).add(chosen_id)
        segments.append(
            {
                "index": seg.index,
                "query": query,
                "text": segment_text(seg),
                "candidates": merged,
                "chosen_id": chosen_id,
            }
        )
    provider_label = clients[0].provider_id if len(clients) == 1 else WEB_AGGREGATE
    return {"provider": provider_label, "segments": segments}


def apply_selection(
    curation: dict[str, Any], selections: dict[int, str]
) -> tuple[dict[str, Any], list[int]]:
    """Apply ``{segment_index: candidate_id}`` to a cached curation blob.

    Validates each candidate_id belongs to that segment's cached candidate list;
    updates ``chosen_id`` in place. Returns ``(updated_blob, invalid_indices)``
    where ``invalid_indices`` lists segment indices whose id was not found.
    """
    by_index = {s["index"]: s for s in curation.get("segments", [])}
    invalid: list[int] = []
    for idx, cand_id in selections.items():
        seg = by_index.get(int(idx))
        if seg is None:
            invalid.append(int(idx))
            continue
        valid_ids = {c.get("id") for c in seg.get("candidates", [])}
        if cand_id not in valid_ids:
            invalid.append(int(idx))
            continue
        seg["chosen_id"] = cand_id
    return curation, invalid


def _chosen_dict_for(curation: dict[str, Any] | None, segment_index: int) -> dict[str, Any] | None:
    """The raw chosen candidate dict for a segment (with ``provider`` key), or None."""
    if not curation:
        return None
    for seg in curation.get("segments", []):
        if seg.get("index") != segment_index:
            continue
        cands = seg.get("candidates", [])
        if not cands:
            return None
        target = seg.get("chosen_id") or cands[0].get("id")
        for c in cands:
            if c.get("id") == target:
                return c
        return cands[0]
    return None


def chosen_candidate_for(
    curation: dict[str, Any] | None, segment_index: int
) -> MediaCandidate | None:
    """Return the chosen :class:`MediaCandidate` for a segment, or None.

    None when there is no curation, the segment is missing, no choice was made, or
    the chosen id no longer matches a cached candidate (the runner then falls back
    to the auto path so a missing choice never hard-blocks a render).
    """
    d = _chosen_dict_for(curation, segment_index)
    return MediaCandidate.from_dict(d) if d is not None else None


def chosen_provider_for(
    curation: dict[str, Any] | None, segment_index: int
) -> str | None:
    """Which web-* provider the chosen candidate came from (for download_chosen).

    Falls back to the curation's top-level ``provider`` when a candidate predates
    the per-candidate ``provider`` key (backward-compat with M2-12 blobs).
    """
    d = _chosen_dict_for(curation, segment_index)
    if d is None:
        return None
    return d.get("provider") or (curation or {}).get("provider")


__all__ = [
    "CANDIDATES_PER_SEGMENT",
    "PHOTOS_PER_SEGMENT",
    "CLIPS_PER_SEGMENT",
    "WEB_AGGREGATE",
    "image_provider_id",
    "web_media_providers",
    "provider_supports_candidates",
    "segment_query",
    "segment_text",
    "build_curation",
    "apply_selection",
    "chosen_candidate_for",
    "chosen_provider_for",
]
