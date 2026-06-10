"""Prompt construction, density math, and chunk planning (module-1 §4/§5/§8/§9).

Pure functions — no I/O, no AI calls — so they are trivially unit-testable.

Pieces:
- :func:`derive_segment_count` / :func:`derive_word_budget` (§5 density tables).
- :func:`plan_chunks` (§8) — split ``n`` segments across the skill structure and
  cap each piece at :data:`SEGMENTS_PER_CHUNK`.
- :func:`segments_json_schema` — the native structured-output schema (§9 happy path).
- :func:`build_phase_a_system` (§4 refine) and :func:`build_chunk_system` /
  :func:`build_chunk_user` (§9 per-chunk script generation).
- :func:`build_youtube_system` / :func:`build_youtube_user` (§7 metadata) +
  :func:`youtube_json_schema`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models.spec import EpisodeSpec, SeriesSpec

from module1.parse import SENTINEL_CLOSE, SENTINEL_OPEN
from module1.skills import SkillTemplate

# --------------------------------------------------------------------------- #
# Tunables (module-1 §5/§8, Open Q #2/#3 — configurable, may be tuned)        #
# --------------------------------------------------------------------------- #
# Seconds of video each image covers, per density tier.
SECONDS_PER_IMAGE: dict[str, int] = {"light": 60, "standard": 35, "dense": 22}
# Words spoken per minute, per language (drives word budget).
WPM_BY_LANGUAGE: dict[str, int] = {"en": 150, "vi": 140}
WPM_DEFAULT = 145

MIN_SEGMENTS = 3
MAX_SEGMENTS = 200  # cap 8 removed (D3); a soft, configurable ceiling.
SEGMENTS_PER_CHUNK = 8  # keep JSON short enough to avoid truncation (§8).


# --------------------------------------------------------------------------- #
# Density → counts / budget (§5)                                              #
# --------------------------------------------------------------------------- #
def derive_segment_count(target_minutes: float, density: str) -> int:
    """``round(target_minutes*60 / seconds_per_image[density])`` clamped to range."""
    spi = SECONDS_PER_IMAGE.get(density, SECONDS_PER_IMAGE["standard"])
    raw = round(target_minutes * 60 / spi)
    return max(MIN_SEGMENTS, min(MAX_SEGMENTS, raw))


def wpm_for(language: str) -> int:
    return WPM_BY_LANGUAGE.get((language or "").lower(), WPM_DEFAULT)


@dataclass(frozen=True)
class WordBudget:
    """Total words + per-segment guidance for a given episode length."""

    total_words: int
    words_per_segment: int


def derive_word_budget(target_minutes: float, language: str, segment_count: int) -> WordBudget:
    """Total words (``target_minutes × wpm``) and an even per-segment hint (§5)."""
    total = round(target_minutes * wpm_for(language))
    per = max(1, round(total / max(1, segment_count)))
    return WordBudget(total_words=total, words_per_segment=per)


# --------------------------------------------------------------------------- #
# Chunk planning (§8)                                                         #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChunkPlan:
    """One unit of work: ``count`` segments of ``section`` starting at ``idx_start``."""

    section: str
    count: int
    idx_start: int

    @property
    def idx_end(self) -> int:
        return self.idx_start + self.count - 1


def _allocate_by_ratio(n: int, sections: list[str], word_ratios: dict[str, float]) -> dict[str, int]:
    """Distribute ``n`` segments across ``sections`` by ratio, summing to ``n``.

    ``word_ratios`` may key by an umbrella label (e.g. ``layers``) rather than by
    each ``layer_*`` section. Such groups are split evenly among their members.
    Uses largest-remainder rounding so the parts always sum to exactly ``n``.
    """
    # Per-section weight: prefer an exact key, else its group key, else even.
    weights: dict[str, float] = {}
    # Build group membership: any section starting with "layer" → "layers".
    for sec in sections:
        if sec in word_ratios:
            weights[sec] = float(word_ratios[sec])
        elif sec.startswith("layer") and "layers" in word_ratios:
            members = [s for s in sections if s.startswith("layer")]
            weights[sec] = float(word_ratios["layers"]) / max(1, len(members))
        else:
            weights[sec] = 0.0

    total_w = sum(weights.values())
    if total_w <= 0:  # no usable ratios → even split
        weights = {sec: 1.0 for sec in sections}
        total_w = float(len(sections))

    # Largest-remainder apportionment, guaranteeing ≥1 per section while n allows.
    exact = {sec: n * w / total_w for sec, w in weights.items()}
    floored = {sec: int(v) for sec, v in exact.items()}
    assigned = sum(floored.values())
    remainder = n - assigned
    # Distribute the remainder to the largest fractional parts.
    order = sorted(sections, key=lambda s: exact[s] - floored[s], reverse=True)
    for sec in order:
        if remainder <= 0:
            break
        floored[sec] += 1
        remainder -= 1
    return floored


def plan_chunks(n: int, structure: list[str], word_ratios: dict[str, float]) -> list[ChunkPlan]:
    """Split ``n`` segments across the skill ``structure``, capped per chunk (§8).

    Returns chunks in narrative order whose ``count`` fields sum to ``n`` and whose
    ``idx_start`` values tile ``1..n`` contiguously, each ``<= SEGMENTS_PER_CHUNK``.
    """
    if n <= 0:
        return []
    sections = list(structure) or ["body"]
    alloc = _allocate_by_ratio(n, sections, word_ratios)

    chunks: list[ChunkPlan] = []
    idx = 1
    for sec in sections:
        count = alloc.get(sec, 0)
        while count > 0:
            take = min(count, SEGMENTS_PER_CHUNK)
            chunks.append(ChunkPlan(section=sec, count=take, idx_start=idx))
            idx += take
            count -= take
    return chunks


# --------------------------------------------------------------------------- #
# JSON schemas (§9)                                                           #
# --------------------------------------------------------------------------- #
def segments_json_schema(language: str) -> dict[str, Any]:
    """Native structured-output schema for one chunk of segments (§9)."""
    return {
        "type": "object",
        "properties": {
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "1-based, contiguous"},
                        "narration": {
                            "type": "string",
                            "description": f"Spoken narration in {language}",
                        },
                        "image_prompt": {
                            "type": "string",
                            "description": "Scene description, ALWAYS English",
                        },
                        "image_label": {
                            "type": "string",
                            "description": "short english slug for the file name",
                        },
                        "image_query": {
                            "type": "string",
                            "description": (
                                "3-7 concrete English search keywords (specific "
                                "nouns) to find a REAL photo of this scene, e.g. "
                                "'Atlantic horseshoe crab beach' or 'red knot bird "
                                "flock Delaware'. No style words."
                            ),
                        },
                    },
                    "required": ["index", "narration", "image_prompt", "image_label"],
                },
            }
        },
        "required": ["segments"],
    }


def youtube_json_schema() -> dict[str, Any]:
    """Structured-output schema for the per-episode YouTube metadata (§7/D7)."""
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "description", "tags"],
    }


# --------------------------------------------------------------------------- #
# Phase A — refine system prompt (§4)                                         #
# --------------------------------------------------------------------------- #
def build_phase_a_system(language: str) -> str:
    """System prompt for the wizard refine chat (§4): propose outline, ask back.

    TOPIC-AGNOSTIC by design (§4): Reelo is a general-purpose video tool, so this
    prompt is a neutral brainstorming assistant for ANY subject (animals, science,
    history, technology, storytelling, …). It must NEVER restrict or refuse a topic
    by genre. The chosen skill's ``rule_prompt_extra`` is a *writing style* and is
    applied only at script generation (``episode_script.py`` / ``build_chunk_system``),
    never here — so the chat does not gate content.
    """
    lines = [
        f"You are a helpful assistant that helps users plan a YouTube video series on "
        f"ANY topic, speaking in {language}.",
        "Help the user shape an engaging idea and propose an outline of episodes (each: a "
        "title + a short description). Work with whatever subject the user brings — animals, "
        "science, history, technology, culture, storytelling, news, and so on. Never refuse "
        "or restrict a topic by genre; you are a general video brainstorming assistant.",
        "If the idea is missing important information (audience, angle, desired depth or "
        "length), ASK a brief clarifying question BEFORE committing to an outline.",
        "Keep the proposed episodes engaging and well-structured (a clear hook, a logical "
        "progression, a satisfying close). The detailed writing style is chosen later at "
        "setup, so here just focus on a strong outline.",
        "Reply in natural prose. AFTER your reply, if you have a concrete outline, append a "
        "lightweight block the UI can parse, exactly in this form:",
        "<<<OUTLINE>>>",
        "1 | Episode title | short description",
        "2 | Episode title | short description",
        "<<<END_OUTLINE>>>",
        "Only include the block when you actually have episodes to propose; otherwise omit it.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Per-chunk script generation prompts (§9)                                    #
# --------------------------------------------------------------------------- #
def build_chunk_system(
    series: SeriesSpec, skill: SkillTemplate, *, use_sentinel: bool = False
) -> str:
    """System prompt for one script chunk (§9): RULE + rule_prompt_extra + language."""
    lines = [
        f"You are a professional scriptwriter for a '{skill.display_name}' YouTube series.",
        f"Write spoken narration in {series.language}. Image prompts are ALWAYS in English.",
        "Return ONLY a JSON object of the form "
        '{"segments": [{"index": int, "narration": str, "image_prompt": str, '
        '"image_label": str, "image_query": str}]}.',
        "Each segment = exactly one narration block + one image. narration is for the ear "
        "(short sentences, no markdown, no stage directions). image_prompt is a concrete "
        "visual scene in English (subject, action, setting, mood). image_label is a short "
        "english slug. image_query is 3-7 concrete English search keywords (specific nouns, "
        "NO style words) that would find a REAL photo of the scene — e.g. "
        '"Atlantic horseshoe crab beach", "red knot bird flock Delaware". '
        "Used when the image source is a real-photo provider; always include it.",
        "Do not include any prose outside the JSON object.",
    ]
    if use_sentinel:
        lines.append(
            f"Wrap the JSON object between the markers {SENTINEL_OPEN} and {SENTINEL_CLOSE}."
        )
    if skill.script.rule_prompt_extra:
        lines.append("")
        lines.append(skill.script.rule_prompt_extra)
    return "\n".join(lines)


def build_chunk_user(
    series: SeriesSpec,
    ep: EpisodeSpec,
    chunk: ChunkPlan,
    *,
    words_per_segment: int,
    prev_summary: str | None = None,
) -> str:
    """User-turn instruction for a single chunk (§8/§9)."""
    desc = (ep.desc or "").strip()
    lines = [
        f"Series: {series.name} — {series.topic}".strip(" —"),
        f"Episode {ep.order}: {ep.title}" + (f" — {desc}" if desc else ""),
        f"Write segments index {chunk.idx_start}..{chunk.idx_end} "
        f"({chunk.count} segment(s)) for the '{chunk.section}' part of the script.",
        f"Aim for about {words_per_segment} words of narration per segment.",
        f"Output exactly {chunk.count} segment(s), with index running "
        f"{chunk.idx_start}..{chunk.idx_end}.",
    ]
    if prev_summary:
        lines.insert(2, f"Continue smoothly from what came before: {prev_summary}")
    return "\n".join(lines)


def retry_note(reason: str, count: int, *, truncated: bool = False) -> str:
    """One-line correction message appended to messages[] on parse/validate failure (§9)."""
    if truncated:
        return (
            f"Your previous output was cut off. Return ONLY valid JSON with exactly {count} "
            f"segment(s); shorten each narration so the JSON is complete."
        )
    return (
        f"Your previous response was invalid: {reason}. Return ONLY valid JSON matching the "
        f"schema, with exactly {count} segment(s)."
    )


# --------------------------------------------------------------------------- #
# YouTube metadata prompts (§7)                                               #
# --------------------------------------------------------------------------- #
def build_youtube_system(series: SeriesSpec) -> str:
    return (
        "You write YouTube metadata. Return ONLY a JSON object "
        '{"title": str, "description": str, "tags": [str]}. '
        f"Write the title and description in {series.language}; keep the title under ~70 "
        "characters; provide 5-12 relevant tags. No prose outside the JSON."
    )


def build_youtube_user(series: SeriesSpec, ep: EpisodeSpec, narration_preview: str) -> str:
    return "\n".join(
        [
            f"Series: {series.name}",
            f"Episode {ep.order}: {ep.title}",
            f"Episode summary: {(ep.desc or '').strip()}",
            "Script excerpt:",
            narration_preview,
        ]
    )


__all__ = [
    "SECONDS_PER_IMAGE",
    "WPM_BY_LANGUAGE",
    "MIN_SEGMENTS",
    "MAX_SEGMENTS",
    "SEGMENTS_PER_CHUNK",
    "derive_segment_count",
    "wpm_for",
    "WordBudget",
    "derive_word_budget",
    "ChunkPlan",
    "plan_chunks",
    "segments_json_schema",
    "youtube_json_schema",
    "build_phase_a_system",
    "build_chunk_system",
    "build_chunk_user",
    "retry_note",
    "build_youtube_system",
    "build_youtube_user",
]
