"""AIClient ABC + cross-module DTOs (Module 3 contract).

This file is the contract Module 1 and Module 2 program against; the concrete
clients (gemini/openai/anthropic/eleven/kie/edge) and the ``ServiceRegistry``
are owned by Module 3 (``reelo-ai-services``) and live elsewhere in
``clients/``. Changing anything here is a cross-module contract change and must
go through the platform-lead (see ``docs/agent-team.md``). Mirrors
``module-3-ai-service-manager.md`` §3.

Key types module owners import from here:
- :class:`Task` — capability enum.
- :class:`AIClient` — base class to subclass per provider.
- :class:`ScriptRequest` / :class:`ScriptResult`, :class:`VoiceRequest` /
  :class:`VoiceResult`, :class:`ImageRequest` / :class:`ImageResult` — call DTOs.
- :class:`CallContext` — ``{user_id, keys, usage}`` threaded through every call.
- :class:`ServiceConfig` — typed view over one ``services.yaml`` block.
- :class:`KeyStore` / :class:`UsageLogger` — Protocols (concrete impls in
  ``keystore.py`` / ``usage.py``) so this module has no runtime deps on them.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Capability enum
# ---------------------------------------------------------------------------
class Task(str, Enum):
    WRITE_SCRIPT = "write-script"
    GENERATE_VOICE = "generate-voice"
    GENERATE_IMAGE = "generate-image"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class NotSupportedError(Exception):
    """The client does not implement the requested capability."""


class ProviderUnavailableError(Exception):
    """Key missing/invalid, service down, or rate-limited — eligible for fallback."""


class InvalidKeyError(ProviderUnavailableError):
    """Authentication failed (401/403). Do NOT fall back to another provider's key."""


# ---------------------------------------------------------------------------
# Auth / config view over a services.yaml block
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AuthConfig:
    """``auth:`` block of a service definition."""

    type: str = "key"  # "key" | "none" (edge-tts)
    key_ref: str | None = None  # logical key id, e.g. "elevenlabs", "google_aistudio"
    env: str | None = None  # env var name to inject for skill-wrapper subprocesses


@dataclass(frozen=True)
class ServiceConfig:
    """Typed view over a single ``services.yaml`` entry.

    Module 3's registry builds one of these per provider and passes it to the
    client constructor. ``raw`` keeps the full untyped dict so clients can read
    provider-specific keys (models, sizes, pricing, endpoint, …).
    """

    provider_id: str
    raw: dict[str, Any]

    @property
    def display_name(self) -> str:
        return self.raw.get("display_name", self.provider_id)

    @property
    def cost_tier(self) -> str:
        return self.raw.get("cost_tier", "paid")  # "free" | "paid"

    @property
    def endpoint(self) -> str | None:
        return self.raw.get("endpoint")

    @property
    def key_help_url(self) -> str | None:
        return self.raw.get("key_help_url")

    @property
    def auth(self) -> AuthConfig:
        a = self.raw.get("auth", {}) or {}
        return AuthConfig(type=a.get("type", "key"), key_ref=a.get("key_ref"), env=a.get("env"))

    @property
    def tasks(self) -> dict[str, Any]:
        return self.raw.get("tasks", {}) or {}

    @property
    def pricing(self) -> dict[str, Any]:
        return self.raw.get("pricing", {}) or {}


# ---------------------------------------------------------------------------
# Request / Result DTOs
# ---------------------------------------------------------------------------
@dataclass
class ScriptRequest:
    messages: list[dict[str, Any]]  # [{role, content}] — caller manages history
    system: str | None = None  # real system prompt (M3-3)
    model: str | None = None
    json_schema: dict[str, Any] | None = None  # native structured output
    temperature: float | None = None


@dataclass
class ScriptResult:
    text: str
    model: str = ""
    usage: dict[str, Any] | None = None  # prompt/completion tokens -> cost
    raw: dict[str, Any] | None = None


@dataclass
class VoiceRequest:
    voice_id: str
    text: str | None = None  # direct text (chunk) ...
    text_file: Path | None = None  # ... or a file
    settings: dict[str, Any] | None = None
    # ---- voice-clone fields (OmniVoice, optional + backward-compatible) -----
    # Only the zero-shot clone provider (omnivoice) reads these; preset TTS
    # clients (edge/eleven) ignore them. ``ref_audio`` is a local wav the worker
    # has already fetched from object storage; ``ref_text`` is its transcript;
    # ``language`` is the target language code/name (600+ languages). All default
    # to None so every existing caller/client is unaffected.
    ref_audio: Path | None = None
    ref_text: str | None = None
    language: str | None = None


@dataclass
class VoiceResult:
    out_path: Path
    duration_s: float | None = None
    chars: int | None = None  # billed character count -> cost
    raw: dict[str, Any] | None = None


@dataclass
class ImageRequest:
    prompt_file: Path | None = None
    prompt: str | None = None  # direct prompt (e.g. thumbnail)
    size: str = "16:9"
    # Short English search keywords for stock/real-photo providers (web-commons,
    # future openverse/pexels). Generative providers (gemini/kie/openai/sd)
    # ignore these and use ``prompt``/``prompt_file``. Backward-compatible: both
    # default to None so existing callers are unaffected.
    query: str | None = None  # 3-7 concrete English nouns to search on
    label: str | None = None  # segment slug, a de-sluggable query fallback


@dataclass
class ImageResult:
    out_path: Path
    count: int = 1  # billed image count -> cost
    # Provider-specific metadata. For web-photo providers (web-commons) this MUST
    # carry the legal attribution block ({title, author, license, source_url,
    # descriptionurl}); the worker persists it (credits.json) so publish/export
    # can show credit. Generative providers leave it None / provider-defined.
    raw: dict[str, Any] | None = None


@dataclass
class MediaCandidate:
    """One media search hit a web-* provider offers for human curation (M2-12/M2-13).

    Web-* providers (``web-commons`` photos; ``web-pexels`` video clips;
    ``supports_candidates = True``) return a list of these per segment so the user
    can hand-pick the on-topic media instead of the system auto-picking. It is
    **metadata + a small preview only** — no large file is downloaded until the
    chosen candidate is materialized via :meth:`AIClient.download_chosen`.

    ``media_type`` makes the same grid mix images and clips per segment (M2-13):
    - ``"image"`` (default — backward-compatible with the old ImageCandidate):
      ``full_url`` is the large (~1600px) raster to download at render time.
    - ``"video"``: the grid shows :attr:`poster_url` with a ▶ badge; at render
      time the renderer downloads :attr:`video_url` (an mp4 file) and fits/loops
      it to the segment duration. :attr:`duration` is the source clip length (s),
      :attr:`preview_url` an optional short clip for hover-play.

    Shared fields (carry the attribution block stored on :attr:`ImageResult.raw`):
    - ``id``: stable per-result id (Commons file title / Pexels video id).
    - ``thumb_url``: small preview URL for the selection grid (image thumb /
      video poster thumb). For video this mirrors ``poster_url``.
    - ``full_url``: large raster (images). For video this mirrors ``video_url``.
    - ``title`` / ``author`` / ``license`` / ``source_url`` / ``descriptionurl``.
    - ``width`` / ``height``: pixel size hint (the chosen render file's size for
      video; the preview size for images).
    """

    id: str
    thumb_url: str
    full_url: str
    title: str
    author: str = "Unknown"
    license: str = "see source"
    source_url: str = ""
    descriptionurl: str = ""
    width: int = 0
    height: int = 0
    # ---- media-aware fields (M2-13) ---------------------------------------
    media_type: str = "image"  # "image" | "video"
    duration: float = 0.0  # source clip length in seconds (video only)
    poster_url: str = ""  # representative image for the grid (video only)
    preview_url: str = ""  # optional short preview clip for hover-play (video)
    video_url: str = ""  # mp4 file to download at render time (video only)

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for JSONB persistence (``Episode.image_curation``) / wire."""
        return {
            "id": self.id,
            "thumb_url": self.thumb_url,
            "full_url": self.full_url,
            "title": self.title,
            "author": self.author,
            "license": self.license,
            "source_url": self.source_url,
            "descriptionurl": self.descriptionurl,
            "width": self.width,
            "height": self.height,
            "media_type": self.media_type,
            "duration": self.duration,
            "poster_url": self.poster_url,
            "preview_url": self.preview_url,
            "video_url": self.video_url,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MediaCandidate":
        return cls(
            id=str(d.get("id", "")),
            thumb_url=str(d.get("thumb_url", "")),
            full_url=str(d.get("full_url", "")),
            title=str(d.get("title", "")),
            author=str(d.get("author", "Unknown")),
            license=str(d.get("license", "see source")),
            source_url=str(d.get("source_url", "")),
            descriptionurl=str(d.get("descriptionurl", "")),
            width=int(d.get("width", 0) or 0),
            height=int(d.get("height", 0) or 0),
            media_type=str(d.get("media_type", "image") or "image"),
            duration=float(d.get("duration", 0.0) or 0.0),
            poster_url=str(d.get("poster_url", "")),
            preview_url=str(d.get("preview_url", "")),
            video_url=str(d.get("video_url", "")),
        )

    @property
    def is_video(self) -> bool:
        return self.media_type == "video"

    def attribution(self) -> dict[str, str]:
        """The {title, author, license, source_url, descriptionurl} credit block."""
        return {
            "title": self.title,
            "author": self.author,
            "license": self.license,
            "source_url": self.source_url,
            "descriptionurl": self.descriptionurl,
        }


# Backward-compatible alias: web-commons + existing tests/callers refer to
# ``ImageCandidate``; it is now the media-aware :class:`MediaCandidate` with
# ``media_type`` defaulting to "image" (M2-13).
ImageCandidate = MediaCandidate


# ---------------------------------------------------------------------------
# Collaborator protocols (concrete impls: keystore.py / usage.py)
# ---------------------------------------------------------------------------
@runtime_checkable
class KeyStore(Protocol):
    """BYOK key storage, encrypted at rest (AES-256-GCM). See ``keystore.py``."""

    def has(self, user_id: str, key_ref: str) -> bool: ...
    def get(self, user_id: str, key_ref: str) -> str | None: ...
    def save(self, user_id: str, key_ref: str, value: str) -> None: ...
    def as_env(self, user_id: str, mapping: dict[str, str]) -> dict[str, str]: ...


@runtime_checkable
class UsageLogger(Protocol):
    """Per-user usage/cost recorder. See ``usage.py``."""

    def record(
        self,
        user_id: str,
        provider: str,
        task: str,
        units: float,
        cost: float | None = None,
    ) -> None: ...


@dataclass
class CallContext:
    """Threaded into every client call so it can read the user's key and log usage.

    Built inside the Arq worker from the ``user_id`` carried on the job payload
    (Module 3 §8). ``keys`` / ``usage`` are async-store-backed in production;
    here they are typed as Protocols to keep ``clients.base`` dependency-free.
    """

    user_id: str
    keys: KeyStore
    usage: UsageLogger
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Base client
# ---------------------------------------------------------------------------
class AIClient(ABC):
    """Base class for every provider client.

    Subclasses declare ``capabilities`` and override only the capability methods
    they implement; the rest raise :class:`NotSupportedError`. Clients are
    stateless and constructed once per process by Module 3's registry.
    """

    provider_id: str
    capabilities: set[Task]
    cost_tier: str  # "free" | "paid"
    requires_key: bool  # True except Edge-TTS
    # Web-photo providers (web-commons) set this True: they offer a per-segment
    # candidate list a human curates (M2-12) instead of auto-picking. Generative
    # providers (gemini/kie/openai/sd) leave it False — no selection step.
    supports_candidates: bool = False

    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.provider_id = config.provider_id
        # Defaults derived from config; subclasses may override the class attrs.
        if not hasattr(self, "cost_tier"):
            self.cost_tier = config.cost_tier
        if not hasattr(self, "requires_key"):
            self.requires_key = config.auth.type != "none"
        if not hasattr(self, "capabilities"):
            self.capabilities = {Task(t) for t in config.tasks.keys() if t in Task._value2member_map_}

    def supports(self, task: Task) -> bool:
        return task in self.capabilities

    async def is_available(self, ctx: CallContext) -> bool:
        """``requires_key`` clients need the user's key present; keyless are always up."""
        if not self.requires_key:
            return True
        key_ref = self.config.auth.key_ref
        if key_ref is None:
            return False
        return ctx.keys.has(ctx.user_id, key_ref)

    async def validate_key(self, ctx: CallContext) -> bool:
        """Cheap test call run on save (M3-5). Default no-op for keyless; override otherwise."""
        if not self.requires_key:
            return True
        raise NotImplementedError(f"{self.provider_id} must implement validate_key")

    # ---- capability methods (override the ones in ``capabilities``) ---------
    async def write_script(self, req: ScriptRequest, ctx: CallContext) -> ScriptResult:
        raise NotSupportedError(f"{self.provider_id} does not support WRITE_SCRIPT")

    async def generate_voice(
        self, req: VoiceRequest, out_path: Path, ctx: CallContext
    ) -> VoiceResult:
        raise NotSupportedError(f"{self.provider_id} does not support GENERATE_VOICE")

    async def generate_image(
        self, req: ImageRequest, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        raise NotSupportedError(f"{self.provider_id} does not support GENERATE_IMAGE")

    # ---- candidate curation (web-photo providers only, M2-12) ---------------
    async def search_candidates(
        self,
        query: str,
        ctx: CallContext,
        *,
        size: str = "16:9",
        limit: int = 9,
        exclude: set[str] | None = None,
    ) -> list[MediaCandidate]:
        """Return up to ``limit`` media candidates for human curation.

        Only providers with ``supports_candidates = True`` (web-commons photos,
        web-pexels clips) override this; the rest raise so callers gate on the
        flag. Downloads NOTHING — just metadata + preview urls.
        """
        raise NotSupportedError(f"{self.provider_id} does not support candidate curation")

    async def download_chosen(
        self, candidate: MediaCandidate, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        """Download a user-chosen candidate's media file to ``out_path``.

        Images write the large raster; video providers write the mp4 clip. Returns
        an :class:`ImageResult` whose ``raw["attribution"]`` carries the credit
        block (and ``raw["media_type"]``) so the worker can persist ``credits.json``
        and render the right way.
        """
        raise NotSupportedError(f"{self.provider_id} does not support candidate curation")


__all__ = [
    "Task",
    "NotSupportedError",
    "ProviderUnavailableError",
    "InvalidKeyError",
    "AuthConfig",
    "ServiceConfig",
    "ScriptRequest",
    "ScriptResult",
    "VoiceRequest",
    "VoiceResult",
    "ImageRequest",
    "ImageResult",
    "MediaCandidate",
    "ImageCandidate",
    "KeyStore",
    "UsageLogger",
    "CallContext",
    "AIClient",
]
