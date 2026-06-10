"""Lazy per-episode script generation (module-1 §7/§8/§9, D2/D5/D7).

``generate_episode_script(series, ep, ctx)`` is the public entrypoint Module 2's
``produce_episode`` (and ``worker.tasks.generate_script``) call as "step 0" when
an episode is not yet ``scripted``. It:

1. derives ``segment_count`` from ``target_minutes × density`` (§5),
2. plans chunks across the skill structure (§8),
3. for each chunk runs the RULE loop: native ``json_schema`` request → parse →
   validate, retrying ≤3 with a one-line correction note (§9),
4. reindexes segments contiguously 1..N,
5. generates YouTube metadata (§7/D7),
6. returns a copy of ``ep`` with ``segments`` + ``youtube`` + ``status='scripted'``.

Availability / key failures (``ProviderUnavailableError`` / ``InvalidKeyError``)
are raised by ``registry.resolve`` / the client and are **not** counted against
the 3 parse retries — they bubble up to the worker (§9, Module 3 handles BYOK).
"""

from __future__ import annotations

import json
import logging

from clients.base import AIClient, CallContext, ScriptRequest, ScriptResult, Task
from clients.registry import ServiceRegistry, get_registry
from models.spec import EpisodeSpec, SegmentSpec, SeriesSpec

from module1.parse import ParseError, ValidationError, parse_chunk, slugify, validate_chunk
from module1.prompt import (
    ChunkPlan,
    build_chunk_system,
    build_chunk_user,
    build_youtube_system,
    build_youtube_user,
    derive_segment_count,
    derive_word_budget,
    plan_chunks,
    retry_note,
    segments_json_schema,
    youtube_json_schema,
)
from module1.skills import SkillTemplate, load_skill_template

log = logging.getLogger("reelo.module1.script")

MAX_PARSE_RETRIES = 3


class ScriptGenerationError(Exception):
    """A chunk could not be produced cleanly within the retry budget (§9)."""


def _looks_truncated(text: str) -> bool:
    """Heuristic: output that never closes its top-level object is likely cut off."""
    return text.count("{") > text.count("}")


async def _run_chunk(
    client: AIClient,
    series: SeriesSpec,
    ep: EpisodeSpec,
    skill: SkillTemplate,
    chunk: ChunkPlan,
    *,
    words_per_segment: int,
    prev_summary: str | None,
    ctx: CallContext,
) -> list[SegmentSpec]:
    """Generate + validate one chunk, retrying ≤3 on parse/validate failure (§9)."""
    schema = segments_json_schema(series.language)
    system = build_chunk_system(series, skill)
    messages: list[dict[str, str]] = [
        {
            "role": "user",
            "content": build_chunk_user(
                series, ep, chunk, words_per_segment=words_per_segment, prev_summary=prev_summary
            ),
        }
    ]

    last_error = "unknown"
    for attempt in range(1, MAX_PARSE_RETRIES + 1):
        req = ScriptRequest(messages=list(messages), system=system, json_schema=schema)
        result: ScriptResult = await client.write_script(req, ctx)
        text = result.text or ""
        try:
            data = parse_chunk(text)
            segments = validate_chunk(data, expected_count=chunk.count, idx_start=chunk.idx_start)
            return segments
        except (ParseError, ValidationError) as exc:
            last_error = str(exc)
            log.info(
                "chunk %s..%s attempt %d/%d failed: %s",
                chunk.idx_start,
                chunk.idx_end,
                attempt,
                MAX_PARSE_RETRIES,
                last_error,
            )
            if attempt < MAX_PARSE_RETRIES:
                # Feed the bad output back + a one-line correction note (§9).
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {
                        "role": "user",
                        "content": retry_note(
                            last_error, chunk.count, truncated=_looks_truncated(text)
                        ),
                    }
                )
    raise ScriptGenerationError(
        f"could not produce a clean chunk for segments {chunk.idx_start}..{chunk.idx_end} "
        f"after {MAX_PARSE_RETRIES} attempts: {last_error}"
    )


def reindex(segments: list[SegmentSpec]) -> list[SegmentSpec]:
    """Renumber segments contiguously 1..N and guarantee a unique, slugged label."""
    seen: dict[str, int] = {}
    out: list[SegmentSpec] = []
    for i, seg in enumerate(segments, start=1):
        label = (seg.image_label or "").strip() or slugify(seg.image_prompt)
        base = label
        if label in seen:
            seen[base] += 1
            label = f"{base}-{seen[base]}"
        else:
            seen[base] = 1
        out.append(seg.model_copy(update={"index": i, "image_label": label}))
    return out


async def _generate_youtube_meta(
    client: AIClient, series: SeriesSpec, ep: EpisodeSpec, segments: list[SegmentSpec], ctx: CallContext
) -> dict[str, object]:
    """Generate ``{title, description, tags}`` (§7/D7); degrade gracefully on failure."""
    preview = "\n".join(s.narration for s in segments[:4])[:1500]
    req = ScriptRequest(
        messages=[{"role": "user", "content": build_youtube_user(series, ep, preview)}],
        system=build_youtube_system(series),
        json_schema=youtube_json_schema(),
    )
    try:
        result = await client.write_script(req, ctx)
        data = parse_chunk(result.text or "")
        title = str(data.get("title") or "").strip() or ep.title
        description = str(data.get("description") or "").strip()
        tags = [str(t) for t in (data.get("tags") or []) if str(t).strip()]
        return {"title": title, "description": description, "tags": tags}
    except (ParseError, json.JSONDecodeError, KeyError, TypeError) as exc:
        # Metadata is non-critical; fall back to a minimal block (§9 / D7).
        log.warning("youtube metadata generation failed (%s); using fallback", exc)
        return {"title": ep.title, "description": (ep.desc or "").strip(), "tags": []}


async def generate_episode_script(
    series: SeriesSpec,
    ep: EpisodeSpec,
    ctx: CallContext,
    *,
    registry: ServiceRegistry | None = None,
) -> EpisodeSpec:
    """Generate the full script for one episode (module-1 §7). Returns an updated copy.

    Idempotent: if ``ep`` already has segments, it is returned unchanged.

    Args:
        series: the owning :class:`SeriesSpec` (config + skill + providers).
        ep: the episode to script (``segments`` empty until now).
        ctx: per-job :class:`CallContext` (user key + usage), built by the worker.
        registry: override the process registry (tests inject one with stubs).

    Returns:
        A copy of ``ep`` with ``segments`` (contiguous 1..N), ``youtube`` metadata,
        and ``status='scripted'``.

    Raises:
        ScriptGenerationError: a chunk failed the parse/validate retry budget.
        ProviderUnavailableError / InvalidKeyError: bubbled from resolve/client.
    """
    if ep.segments:  # already scripted (§7 idempotency)
        return ep

    reg = registry or get_registry()
    target_minutes = ep.target_minutes or series.target_minutes
    n = derive_segment_count(target_minutes, series.density)
    budget = derive_word_budget(target_minutes, series.language, n)
    skill = load_skill_template(series.skill)
    chunks = plan_chunks(n, skill.script.structure, skill.script.word_ratios)

    provider = series.providers.get("script", "")
    client = await reg.resolve(Task.WRITE_SCRIPT, provider, ctx)

    segments: list[SegmentSpec] = []
    prev_summary: str | None = None
    for chunk in chunks:
        chunk_segs = await _run_chunk(
            client,
            series,
            ep,
            skill,
            chunk,
            words_per_segment=budget.words_per_segment,
            prev_summary=prev_summary,
            ctx=ctx,
        )
        segments.extend(chunk_segs)
        # Carry a 1-2 sentence tail forward to keep narrative continuity (§8).
        if chunk_segs:
            prev_summary = chunk_segs[-1].narration.strip()[:300]

    segments = reindex(segments)
    youtube = await _generate_youtube_meta(client, series, ep, segments, ctx)

    return ep.model_copy(
        update={"segments": segments, "youtube": youtube, "status": "scripted"}
    )


__all__ = [
    "MAX_PARSE_RETRIES",
    "ScriptGenerationError",
    "reindex",
    "generate_episode_script",
]
