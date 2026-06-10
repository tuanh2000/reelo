"""Cross-module domain contracts (the SeriesSpec family).

This is the canonical shape that Module 1 (scriptwriting) *produces*, the DB
persists in ``series.spec_json`` (JSONB), and Module 2 (video-generator)
*consumes*. It mirrors ``module-1-ai-chatting.md`` §11 exactly.

Changing any model here is a cross-module contract change and must go through
the platform-lead (see ``docs/agent-team.md``). Module owners refine *logic*
that fills these in; they keep the *shape*.

Field semantics (load-bearing):
- ``SegmentSpec.index`` is 1-based and becomes the zero-padded file prefix
  ``NN_`` used by the materializer. The hard invariant when an episode is
  scripted: ``index`` is contiguous ``1..len(segments)`` and
  ``len(segments) == segment_count`` derived from ``target_minutes × density``.
- ``narration`` is in ``SeriesSpec.language``; ``image_prompt`` is ALWAYS English (D1).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Aspect = Literal["16:9", "9:16"]
Density = Literal["light", "standard", "dense"]
Skill = Literal["religion", "story", "explain", "news"]
EpisodeStatus = Literal["draft", "scripted", "assets", "assembled", "published"]


class SegmentSpec(BaseModel):
    """One narration block + one image. Maps to UI ``ScriptSegment``."""

    index: int = Field(..., ge=1, description="1-based; file prefix NN_")
    narration: str = Field(..., description="Speech in SeriesSpec.language -> one '===' block")
    image_prompt: str = Field(..., description="Scene-specific, ALWAYS English (D1)")
    image_label: str = Field(..., description="English slug used for the file name")
    image_query: str | None = Field(
        default=None,
        description=(
            "Short English search keywords (3-7 concrete nouns) for real-photo "
            "providers (web-commons). Optional, backward-compatible: None when "
            "the model omits it or for generative image providers."
        ),
    )


class EpisodeSpec(BaseModel):
    """An episode within a series. ``segments`` stays empty until scripted (lazy gen, D2)."""

    episode_id: str
    title: str
    order: int
    desc: str | None = None
    target_minutes: float | None = None  # inherits series; may override
    status: EpisodeStatus = "draft"
    youtube: dict[str, Any] | None = None  # {title, description, tags[]} — lazy (D7)
    segments: list[SegmentSpec] = Field(default_factory=list)


class ImageStyle(BaseModel):
    """Resolved visual style = preset (base_prompt) + skill template (style_layer) (D4)."""

    preset_id: str
    base_prompt: str  # from preset (visual)
    palette: list[str] = Field(default_factory=list)
    description: str = ""
    aspect: Aspect = "16:9"  # D8
    style_layer: str | None = None  # from skill template (D4)


class VoiceSample(BaseModel):
    """An uploaded reference clip for zero-shot voice cloning (OmniVoice).

    Stored OUT of any provider key — Reelo hosts the GPU, so cloning is keyless
    from the user's side. ``audio_key`` is the object-storage key of the (wav
    24 kHz mono) sample; ``transcript`` is the exact text spoken in it;
    ``language`` is the sample's language code/name (defaults to the series
    language at produce time when omitted).
    """

    audio_key: str
    transcript: str
    language: str | None = None


class VoiceConfig(BaseModel):
    """TTS configuration carried at the series level.

    ``mode`` selects the synthesis style (backward-compatible default
    ``"preset"`` = a fixed provider voice by ``voice_id``, e.g. edge/eleven).
    ``"clone"`` is the OmniVoice zero-shot path: it ignores ``voice_id`` and
    clones the uploaded :class:`VoiceSample` (``voice_sample``). Existing specs
    deserialize unchanged (both new fields are optional with safe defaults).
    """

    provider: str
    voice_id: str
    settings: dict[str, Any] | None = None
    mode: Literal["preset", "clone"] = "preset"
    voice_sample: VoiceSample | None = None


class SeriesSpec(BaseModel):
    """The full series contract. Persisted as ``series.spec_json`` (JSONB)."""

    schema_version: int = 1
    series_id: str
    name: str
    topic: str
    skill: Skill
    language: str  # D1 (speech language; image prompts stay English)
    target_minutes: float  # D5 (default per episode)
    density: Density  # D5
    providers: dict[str, str]  # {script, image, voice}
    image_style: ImageStyle
    voice: VoiceConfig
    episodes: list[EpisodeSpec] = Field(default_factory=list)
    music: dict[str, Any] | None = None  # {path, volume?, ducking?} — optional (M2-1)
    subtitles: dict[str, Any] | None = None  # SRT always generated (M2-2); field reserved


__all__ = [
    "Aspect",
    "Density",
    "Skill",
    "EpisodeStatus",
    "SegmentSpec",
    "EpisodeSpec",
    "ImageStyle",
    "VoiceSample",
    "VoiceConfig",
    "SeriesSpec",
]
