"""HTTP request/response models for the REST surface.

These match the 9 ``reelo-ui/lib/api.ts`` functions + Module 1/2/3 endpoint
specs. They are the wire contract; the domain ``SeriesSpec`` family lives in
:mod:`models.spec` and is referenced where a full spec crosses the boundary.

Shapes here are owned by the platform-lead; module owners fill in handler logic
but keep these request/response shapes (UI depends on them).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from models.jobs import GenJob
from models.spec import Density, EpisodeSpec, ImageStyle, SeriesSpec, Skill, VoiceConfig


# --------------------------------------------------------------------------- #
# Wizard (Module 1)                                                           #
# --------------------------------------------------------------------------- #
class ChatMessage(BaseModel):
    role: Literal["ai", "user"]  # matches UI history shape
    text: str


class OutlineItemModel(BaseModel):
    id: str
    title: str
    desc: str
    pick: bool = True


class WizardMessageRequest(BaseModel):
    """Body of ``POST /wizard/message`` (maps ``sendWizardMessage(topic, history)``).

    ``skill`` / ``language`` / ``provider`` are optional so Phase A honours the
    Setup screen's current selection if the user has already picked one (Module 1
    Open Q #1). When omitted the handler keeps ``run_phase_a``'s safe defaults.
    """

    idea: str = Field(..., description="Free-form idea / latest user turn")
    history: list[ChatMessage] = Field(default_factory=list)
    skill: Skill | None = Field(default=None, description="Setup skill, if chosen")
    language: str | None = Field(default=None, description="Setup language, if chosen")
    provider: str | None = Field(
        default=None, description="Setup script provider id, if chosen"
    )


class WizardMessageResponse(BaseModel):
    """Maps UI ``ChatTurn`` ``{reply, outline?}``."""

    reply: str
    outline: list[OutlineItemModel] | None = None


class SeriesConfig(BaseModel):
    """Setup-screen config carried into approve (Module 1 §6).

    ``providers`` is deprecated/ignored: provider choices are account-level
    (Settings page) and snapshotted server-side at approve. It is kept optional
    for backward compatibility with older clients but no longer read.
    """

    skill: Skill
    language: str
    target_minutes: float = 10
    density: Density = "standard"
    aspect: Literal["16:9", "9:16"] = "16:9"
    providers: dict[str, str] | None = None  # deprecated; ignored (account-level)
    voice: VoiceConfig
    image_style: ImageStyle


class WizardApproveRequest(BaseModel):
    """Body of ``POST /wizard/approve``."""

    name: str
    topic: str = ""
    outline: list[OutlineItemModel]
    config: SeriesConfig


class WizardApproveResponse(BaseModel):
    series: SeriesSpec


# --------------------------------------------------------------------------- #
# Episodes / script (Module 1)                                                #
# --------------------------------------------------------------------------- #
class EpisodeScriptResponse(BaseModel):
    episode: EpisodeSpec


class EpisodeAssets(BaseModel):
    """Signed asset URLs for an assembled episode (review screen player + thumbs).

    All optional: an episode that is not assembled yet has none. ``thumbnails`` is
    a list (0..3) of signed URLs in stable order so the UI can index by
    ``thumbnailIndex``.
    """

    video_url: str | None = Field(default=None, alias="videoUrl")
    srt_url: str | None = Field(default=None, alias="srtUrl")
    thumbnails: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class EpisodeDetailResponse(BaseModel):
    """``GET /episodes/{id}`` — full episode spec + signed asset URLs + series id.

    Lets the UI (project/workspace/review) refetch a single episode's current
    ``status`` / ``segments`` / ``youtube`` (after lazy script gen or produce) and,
    once assembled, play the rendered video + show thumbnails without first
    POSTing to ``/publish/export``.
    """

    series_id: str
    episode: EpisodeSpec
    assets: EpisodeAssets = Field(default_factory=EpisodeAssets)
    # Lazy script-gen progress so the UI never spins forever (surfaced from the
    # episode's ``paths`` JSONB, written by ``worker.tasks.generate_script``).
    # ``script_status`` is "running" | "done" | "error" | None (never generated);
    # ``script_error`` carries a short, copyable message only when status="error".
    script_status: Literal["running", "done", "error"] | None = None
    script_error: str | None = None


# --------------------------------------------------------------------------- #
# Style (Module 1)                                                            #
# --------------------------------------------------------------------------- #
class InferStyleResponse(BaseModel):
    """Maps ``inferStyle(referenceImages) -> {palette, description}``."""

    palette: list[str]
    description: str


# --------------------------------------------------------------------------- #
# Series CRUD (Module 1)                                                      #
# --------------------------------------------------------------------------- #
class SeriesListResponse(BaseModel):
    series: list[SeriesSpec]


class SaveSeriesRequest(BaseModel):
    series: SeriesSpec


class SaveSeriesResponse(BaseModel):
    series: SeriesSpec


class RenameSeriesRequest(BaseModel):
    """``PATCH /series/{id}`` — rename only.

    ``name`` is trimmed and length-validated (1–120 chars after stripping) so the
    UI's inline rename can send a bare ``{name}`` without round-tripping the whole
    spec. Leading/trailing whitespace is stripped by the validator.
    """

    name: str = Field(..., min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("name must not be empty")
        if len(trimmed) > 120:
            raise ValueError("name must be at most 120 characters")
        return trimmed


# --------------------------------------------------------------------------- #
# Generation (Module 2)                                                       #
# --------------------------------------------------------------------------- #
class StartGenerationRequest(BaseModel):
    series_id: str
    episode_id: str


class CostEstimate(BaseModel):
    images: int = 0
    voice_chars: int = 0
    estimated_cost: float | None = None
    note: str | None = None


class StartGenerationResponse(BaseModel):
    """Maps ``startGeneration -> {jobId}`` (+ cost estimate, Module 2 §13)."""

    job_id: str = Field(..., alias="jobId")
    cost_estimate: CostEstimate | None = None

    model_config = {"populate_by_name": True}


class PollGenerationResponse(BaseModel):
    """Maps ``pollGeneration(jobId) -> GenJob[]``."""

    jobs: list[GenJob]


class MusicUploadResponse(BaseModel):
    path: str


class VoiceSampleResponse(BaseModel):
    """``POST /series/{id}/voice-sample`` — voice-clone reference uploaded.

    Returns the object-storage key of the normalized (wav 24 kHz mono) sample,
    its measured duration, and the resulting :class:`VoiceConfig` (now
    ``mode="clone"`` with ``voice_sample``) so the UI can reflect the change.
    """

    audio_key: str
    duration_s: float
    voice: VoiceConfig


# --------------------------------------------------------------------------- #
# Media curation (M2-12 / M2-13) — web-* candidate selection (photo OR clip)   #
# --------------------------------------------------------------------------- #
class ImageCandidateModel(BaseModel):
    """One media candidate for a segment (web-commons photo OR web-pexels clip).

    Preview-only. ``media_type="image"`` is the default (backward-compatible).
    For ``media_type="video"`` the UI shows ``poster_url`` with a ▶ badge and may
    hover-play ``preview_url``; ``video_url`` is the mp4 downloaded at render time.
    ``provider`` records which web-* source the candidate came from (M2-13 merge).
    """

    id: str
    thumb_url: str
    full_url: str
    title: str = ""
    author: str = "Unknown"
    license: str = "see source"
    source_url: str = ""
    descriptionurl: str = ""
    width: int = 0
    height: int = 0
    # ---- media-aware fields (M2-13) ----
    media_type: Literal["image", "video"] = "image"
    duration: float = 0.0
    poster_url: str = ""
    preview_url: str = ""
    video_url: str = ""
    provider: str | None = None  # "web-commons" | "web-pexels"


class SegmentCandidatesModel(BaseModel):
    """Candidate list + current choice for one segment."""

    index: int
    query: str
    text: str = ""  # short narration preview shown above the grid
    candidates: list[ImageCandidateModel] = Field(default_factory=list)
    chosen_id: str | None = None


class ImageCandidatesResponse(BaseModel):
    """``GET /episodes/{id}/image-candidates`` — per-segment candidate grids."""

    provider: str
    segments: list[SegmentCandidatesModel] = Field(default_factory=list)


class ImageSelectionRequest(BaseModel):
    """``POST /episodes/{id}/image-selection`` — {segment_index: candidate_id}."""

    selections: dict[int, str] = Field(
        default_factory=dict,
        description="map segment index -> chosen candidate id (must be cached)",
    )


# --------------------------------------------------------------------------- #
# Publish / export (Module 2)                                                 #
# --------------------------------------------------------------------------- #
class PublishMeta(BaseModel):
    title: str
    description: str
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["public", "unlisted", "private"] = "public"
    thumbnail_index: int = Field(default=0, alias="thumbnailIndex")

    model_config = {"populate_by_name": True}


class PublishExportRequest(BaseModel):
    series_id: str
    episode_id: str
    meta: PublishMeta


class PublishExportResponse(BaseModel):
    """Module 2 §12 export shape (signed URLs)."""

    video_url: str = Field(..., alias="videoUrl")
    srt_url: str | None = Field(default=None, alias="srtUrl")
    thumbnail_url: str | None = Field(default=None, alias="thumbnailUrl")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


# --------------------------------------------------------------------------- #
# Providers / keys / usage (Module 3)                                         #
# --------------------------------------------------------------------------- #
class ProviderOption(BaseModel):
    """One provider for a task — UI Setup uses these (replaces old PROVIDERS)."""

    id: str
    name: str
    cost_tier: Literal["free", "paid"]
    requires_key: bool
    key_help_url: str | None = None
    note: str | None = None


class ProvidersResponse(BaseModel):
    """``GET /providers`` derived from services.yaml, grouped by task."""

    script: list[ProviderOption] = Field(default_factory=list)
    image: list[ProviderOption] = Field(default_factory=list)
    voice: list[ProviderOption] = Field(default_factory=list)


class ProviderSettingsItem(BaseModel):
    """A single task's account-level provider choice + readiness (Settings page).

    ``provider`` is the chosen provider id (``None`` when the user has not picked
    one yet). ``requires_key`` / ``has_key`` mirror the key state for that
    provider; ``ready`` is the gate the UI uses = a provider is chosen AND
    (it needs no key OR a key is present) AND (it needs no voice sample OR one is
    present).

    ``requires_sample`` / ``has_sample`` are voice-only: the OmniVoice clone
    provider requires an uploaded reference sample (account-level, see
    ``POST /settings/voice-sample``). They stay ``False`` for non-voice tasks and
    for voice providers that do not clone (edge / eleven).
    """

    provider: str | None = None
    requires_key: bool = False
    has_key: bool = False
    requires_sample: bool = False
    has_sample: bool = False
    ready: bool = False


class ProviderSettingsResponse(BaseModel):
    """``GET /settings/providers`` — chosen providers, readiness, and catalog.

    ``options`` is the same per-task catalog as ``GET /providers`` so the
    Settings page can render dropdowns without a second call.
    """

    script: ProviderSettingsItem
    image: ProviderSettingsItem
    voice: ProviderSettingsItem
    script_ready: bool = False
    image_ready: bool = False
    voice_ready: bool = False
    options: ProvidersResponse


class VoiceSampleStatusResponse(BaseModel):
    """``GET /settings/voice-sample`` / ``POST /settings/voice-sample`` result.

    Reports whether the account has an OmniVoice voice-clone reference uploaded,
    plus its ``transcript`` / ``language`` for display. NEVER returns the audio
    bytes; ``duration_s`` is set on a fresh upload (the normalized clip length).
    """

    has_sample: bool = False
    transcript: str | None = None
    language: str | None = None
    duration_s: float | None = None


class SaveProviderSettingsRequest(BaseModel):
    """``PUT /settings/providers`` — partial update of the chosen providers.

    Each field is optional: only the supplied tasks are updated. A provider id
    must be valid for its task (validated server-side); ``None`` is allowed to
    clear a choice.
    """

    script: str | None = None
    image: str | None = None
    voice: str | None = None

    model_config = {"extra": "ignore"}


class SaveKeyRequest(BaseModel):
    """Maps ``saveApiKey(provider, key)``."""

    provider: str
    key: str


class SaveKeyResponse(BaseModel):
    key_ref: str
    valid: bool | None = None


class KeyStatus(BaseModel):
    present: bool
    valid: bool | None = None


class KeysStatusResponse(BaseModel):
    """``GET /keys/status`` — presence/validity only, NEVER the key value."""

    keys: dict[str, KeyStatus]


class UsageRow(BaseModel):
    provider: str
    task: str
    units: float
    cost: float | None = None
    ts: str


class UsageResponse(BaseModel):
    usage: list[UsageRow]
    total_cost: float | None = None
