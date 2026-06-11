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
    series toolset the user has already picked in the create flow (the script
    provider is PER-SERIES, chosen up-front). When ``provider`` is omitted the
    handler falls back to the first key-ready script provider for the user (and a
    keyless dev stub when none qualifies).
    """

    idea: str = Field(..., description="Free-form idea / latest user turn")
    history: list[ChatMessage] = Field(default_factory=list)
    skill: Skill | None = Field(default=None, description="Setup skill, if chosen")
    language: str | None = Field(default=None, description="Setup language, if chosen")
    provider: str | None = Field(
        default=None, description="Per-series script provider id, if chosen"
    )


class WizardMessageResponse(BaseModel):
    """Maps UI ``ChatTurn`` ``{reply, outline?}``."""

    reply: str
    outline: list[OutlineItemModel] | None = None


class SeriesConfig(BaseModel):
    """Setup-screen config carried into approve (Module 1 §6).

    ``providers`` is the PER-SERIES toolset the user picked in the create flow
    ({script, image, voice}). It is set straight into ``SeriesSpec.providers`` at
    approve (and aligns ``voice.provider``). When omitted (older clients), the
    handler falls back to the keyless dev defaults so the flow never hard-fails;
    the readiness gate (per-series key check) still applies before chat/produce.
    """

    skill: Skill
    language: str
    target_minutes: float = 10
    density: Density = "standard"
    aspect: Literal["16:9", "9:16"] = "16:9"
    providers: dict[str, str] | None = None  # per-series toolset {script,image,voice}
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


class EpisodeCancelScriptResponse(BaseModel):
    """``POST /episodes/{id}/cancel-script`` — stop an in-flight script gen.

    ``cancelled`` is True when a running script gen was flagged to stop (the worker
    aborts before its next model call, so it stops spending tokens); False when
    there was nothing in flight to stop. ``script_status`` echoes the episode's
    current state so the UI can reconcile without a second fetch.
    """

    cancelled: bool
    script_status: Literal["running", "done", "error", "cancelled"] | None = None


class EpisodeResetResponse(BaseModel):
    """``POST /episodes/{id}/reset`` — the episode after a destructive reset.

    ``episode`` is back to outline-only (no ``segments``, ``status="draft"``). The
    counts report what was wiped (deleted gen_jobs + storage objects) so the UI /
    logs can confirm the cleanup happened.
    """

    episode: EpisodeSpec
    jobs_deleted: int = 0
    assets_deleted: int = 0


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


class GenerationLookup(BaseModel):
    """Most-recent / active produce job for an episode (workspace state recovery).

    Returned on ``GET /episodes/{id}`` so the workspace can rebuild the "đang sản
    xuất" view from the BACKEND (the source of truth) after a tab-switch /
    navigate-away / refresh, without the client holding the ``jobId``. ``None`` on
    the episode response when the episode has never been produced.

    - ``job_id`` — the parent ``gen_jobs`` id to poll (``GET /generation/{jobId}``).
    - ``state`` — "running" while any child is queued/running (or the parent is),
      "error" if it failed, "done" once finished.
    - ``started_at`` — ISO-8601 server timestamp (the parent's ``created_at``) so
      the UI computes produce elapsed = now − started_at on server time.
    - ``jobs`` — the child :class:`GenJob` list (same shape as the poll endpoint)
      so the producing view renders immediately on mount, before the first poll.
    """

    job_id: str = Field(..., alias="jobId")
    state: Literal["running", "done", "error"]
    started_at: str | None = None
    jobs: list[GenJob] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ResumeProductionResponse(BaseModel):
    """``POST /episodes/{id}/resume-production`` — re-run the unfinished steps.

    Re-queues every NON-``done`` child of the latest produce job (``queued`` /
    ``running`` / ``error`` → ``queued``), keeps the ``done`` ones, and re-enqueues
    ``produce_episode``. Used to recover a run that froze (e.g. the worker was
    restarted by a deploy mid-produce, ``max_tries=1`` so Arq won't auto-retry).

    - ``generation`` — the refreshed produce-job lookup (same shape the workspace
      already consumes from ``GET /episodes/{id}``) so the UI can resume polling.
    - ``requeued`` — how many child steps were put back in the queue.
    """

    generation: GenerationLookup
    requeued: int = 0


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
    # ``script_status`` is "running" | "done" | "error" | "cancelled" | None (never
    # generated); ``script_error`` carries a short, copyable message when status is
    # "error" (provider + cause) or "cancelled" (the stop notice).
    script_status: Literal["running", "done", "error", "cancelled"] | None = None
    script_error: str | None = None
    # ISO-8601 server timestamp stamped when script-gen entered ``running`` (so the
    # workspace timer is anchored to server time, not a client mount clock). None
    # unless script gen is/was running.
    script_started_at: str | None = None
    # Most-recent produce job for this episode (state recovery), or None if never
    # produced. The workspace derives its stage from this instead of client state.
    generation: GenerationLookup | None = None


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
    """Maps ``pollGeneration(jobId) -> GenJob[]`` (+ parent start time).

    ``started_at`` is the ISO-8601 server timestamp the parent produce job was
    seeded (its ``created_at``), so the workspace computes produce elapsed = now −
    started_at on SERVER time — stable across tab-switch / remount, unaffected by a
    throttled background timer. ``None`` if the parent has no recorded timestamp.
    """

    jobs: list[GenJob]
    started_at: str | None = None


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
# Custom voice library (shared OmniVoice voice-clone references)               #
# --------------------------------------------------------------------------- #
class CustomVoiceItem(BaseModel):
    """One entry in the shared OmniVoice voice-clone library.

    A cross-tenant, read-public catalog item: any logged-in user sees every
    voice so they can reuse it instead of re-uploading a reference clip. We do
    NOT expose the creator's identity (only ``is_owner`` for the requester) nor
    the raw audio key — the clip is fetched through ``GET /voices/{id}/preview``.
    ``transcript`` is returned so the UI can show what the sample says.
    """

    id: str
    name: str
    language: str | None = None
    transcript: str
    duration_s: float | None = None
    is_owner: bool = False
    created_at: str | None = None


class CustomVoiceListResponse(BaseModel):
    voices: list[CustomVoiceItem]


class CustomVoicePreviewResponse(BaseModel):
    """A time-limited URL to listen to a library voice's reference clip."""

    url: str


class ApplyCustomVoiceRequest(BaseModel):
    """Body of ``POST /series/{id}/voice/custom`` — pick a library voice."""

    voice_id: str


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


class ProviderKeyItem(BaseModel):
    """Per-provider key status for the "Cấu hình AI" key-management page.

    Provider choices are PER-SERIES now (picked in the create/Setup flow); this
    page only manages the user's BYOK keys, which stay PER-USER (entered once per
    provider, reused across every series). ``requires_key`` mirrors the provider's
    ``auth.type``; ``has_key`` / ``valid`` reflect the stored key (encrypted, per
    user). ``key_ref`` is the storage key under which the key lives.
    """

    id: str
    name: str
    task: Literal["script", "image", "voice"]
    cost_tier: Literal["free", "paid"]
    requires_key: bool = False
    has_key: bool = False
    valid: bool | None = None
    key_ref: str | None = None
    key_help_url: str | None = None
    note: str | None = None


class ProviderKeysResponse(BaseModel):
    """``GET /settings/providers`` — per-task provider key catalog + key status.

    The "Cấu hình AI" page lists every provider grouped by task and shows, for the
    ones that need a key, whether the user has saved one (and whether it
    validated). It NO LONGER stores an account-level "default provider" — that
    choice is per-series. Voice-clone samples are also per-series now (uploaded via
    ``POST /series/{id}/voice-sample``), so this response carries no sample state.
    """

    script: list[ProviderKeyItem] = Field(default_factory=list)
    image: list[ProviderKeyItem] = Field(default_factory=list)
    voice: list[ProviderKeyItem] = Field(default_factory=list)


class SeriesReadinessResponse(BaseModel):
    """``GET /series/{id}/readiness`` — can this series chat/produce yet?

    A series is ready when its chosen script + image providers have a per-user key
    (keyless providers count as ready) and, when the voice provider is OmniVoice
    clone, a per-series voice sample is present. ``missing`` lists the human
    messages for whatever blocks readiness so the UI can route the user to the key
    page / the voice-sample upload.
    """

    series_id: str
    script_ready: bool = False
    image_ready: bool = False
    voice_ready: bool = False
    ready: bool = False
    missing: list[str] = Field(default_factory=list)


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


class VoicePauseState(BaseModel):
    """Global voice-pause flag (``GET`` / ``POST /settings/voice-pause``).

    When ``paused`` is true, the worker holds every voice job at the next chunk
    boundary so the shared local GPU (OmniVoice) isn't slammed while several videos
    produce at once. Image generation + render are unaffected.
    """

    paused: bool = False
