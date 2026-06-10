"""Account-level settings (provider configuration + voice-clone sample).

The user configures the AI providers for the three generation tasks
(script / image / voice) ONCE, here, before creating any series. A single
provider set is shared across every series the user owns (decision: account-
level config). Series creation gates on this being ready (script + image
configured), so the UI can route the user here when it is not.

- ``GET /settings/providers``  → chosen providers + per-task readiness + catalog.
- ``PUT /settings/providers``  → upsert the chosen providers (partial).
- ``POST /settings/voice-sample`` → upload the account-level OmniVoice voice-clone
  reference (multipart audio + transcript + language); normalized + stored once.
- ``GET /settings/voice-sample`` → whether a sample is present (+ transcript /
  language; never the audio bytes).

Key storage stays in ``POST /keys`` (per-provider, encrypted per-user); this
router only records *which* provider the user picked and reports whether its
key (if any) and voice sample (if any) are present.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from db.repository import ApiKeyRepo, UserSettingsRepo
from module2 import ffmpeg
from storage import get_storage
from web._provider_keys import (
    provider_requires_key,
    provider_requires_sample,
    provider_supports_field,
    resolve_key_ref,
)
from web._voice_sample import (
    VOICE_SAMPLE_MAX_S,
    VOICE_SAMPLE_MIN_S,
    normalize_voice_sample,
)
from web.deps import CurrentUser, DbSession
from web.routers.providers import build_provider_catalog
from web.schemas import (
    ProviderSettingsItem,
    ProviderSettingsResponse,
    SaveProviderSettingsRequest,
    VoiceSampleStatusResponse,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _present_key_refs(rows) -> set[str]:
    return {row.key_ref for row in rows}


def _item_for(
    provider: str | None, present_refs: set[str], *, has_sample: bool = False
) -> ProviderSettingsItem:
    """Build the per-task readiness item for a chosen provider.

    ``ready`` = a provider is chosen AND (it needs no key OR a key is present)
    AND (it needs no voice sample OR a sample is present). The aggregate web
    image alias ``"web"`` is keyless (web-commons), so it is always ready once
    chosen. ``requires_sample`` / ``has_sample`` only matter for the voice task
    (OmniVoice clone); callers pass ``has_sample`` for the voice item.
    """
    if not provider:
        return ProviderSettingsItem(
            provider=None,
            requires_key=False,
            has_key=False,
            requires_sample=False,
            has_sample=False,
            ready=False,
        )
    requires_key = provider_requires_key(provider)
    has_key = (resolve_key_ref(provider) in present_refs) if requires_key else False
    requires_sample = provider_requires_sample(provider)
    sample_ok = (not requires_sample) or has_sample
    ready = ((not requires_key) or has_key) and sample_ok
    return ProviderSettingsItem(
        provider=provider,
        requires_key=requires_key,
        has_key=has_key,
        requires_sample=requires_sample,
        has_sample=has_sample if requires_sample else False,
        ready=ready,
    )


@router.get("/providers", response_model=ProviderSettingsResponse)
async def get_provider_settings(
    user_id: CurrentUser, db: DbSession
) -> ProviderSettingsResponse:
    """Return the user's chosen providers + readiness + the per-task catalog."""
    repo = UserSettingsRepo(db)
    providers = await repo.get_providers(user_id)
    present = _present_key_refs(await ApiKeyRepo(db).list_refs(user_id))
    has_sample = (await repo.get_voice_sample(user_id)) is not None

    script = _item_for(providers.get("script"), present)
    image = _item_for(providers.get("image"), present)
    voice = _item_for(providers.get("voice"), present, has_sample=has_sample)

    return ProviderSettingsResponse(
        script=script,
        image=image,
        voice=voice,
        script_ready=script.ready,
        image_ready=image.ready,
        voice_ready=voice.ready,
        options=build_provider_catalog(),
    )


@router.put("/providers", response_model=ProviderSettingsResponse)
async def put_provider_settings(
    body: SaveProviderSettingsRequest, user_id: CurrentUser, db: DbSession
) -> ProviderSettingsResponse:
    """Upsert the chosen providers (partial). Validates id supports its task."""
    updates: dict[str, str | None] = {}
    for field in ("script", "image", "voice"):
        value = getattr(body, field)
        if value is None:
            continue  # field omitted (or explicitly cleared via empty handled below)
        value = value.strip()
        if value == "":
            updates[field] = None  # clear the choice
            continue
        if not provider_supports_field(value, field):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provider '{value}' does not support task '{field}'.",
            )
        updates[field] = value

    if updates:
        await UserSettingsRepo(db).set_providers(user_id, updates)

    return await get_provider_settings(user_id, db)


# --------------------------------------------------------------------------- #
# Voice-clone reference sample (account-level, OmniVoice)                      #
# --------------------------------------------------------------------------- #
def _voice_sample_status(
    sample: dict | None, *, duration_s: float | None = None
) -> VoiceSampleStatusResponse:
    """Project the stored sample blob onto the public status shape (no audio)."""
    if not sample:
        return VoiceSampleStatusResponse(has_sample=False)
    return VoiceSampleStatusResponse(
        has_sample=True,
        transcript=sample.get("transcript"),
        language=sample.get("language"),
        duration_s=duration_s,
    )


@router.get("/voice-sample", response_model=VoiceSampleStatusResponse)
async def get_voice_sample(
    user_id: CurrentUser, db: DbSession
) -> VoiceSampleStatusResponse:
    """Return whether the account has a voice-clone sample (+ transcript/lang).

    Never returns the audio bytes — only presence + display metadata.
    """
    sample = await UserSettingsRepo(db).get_voice_sample(user_id)
    return _voice_sample_status(sample)


@router.post("/voice-sample", response_model=VoiceSampleStatusResponse)
async def upload_voice_sample(
    user_id: CurrentUser,
    db: DbSession,
    audio: UploadFile = File(...),
    transcript: str = Form(...),
    language: str = Form(default=""),
) -> VoiceSampleStatusResponse:
    """Upload the account-level OmniVoice voice-clone reference (multipart).

    Normalizes the upload to a wav 24 kHz mono clip (ffmpeg), validates the
    duration is within 3–30 s, stores it under
    ``voice-samples/<user_id>/sample.wav`` in object storage, and records the
    key + transcript + language in ``user_settings.voice_sample``. Used once per
    account and snapshotted into each series at approve time when the chosen
    voice provider is OmniVoice. Returns presence + transcript/language only
    (NEVER the audio bytes).
    """
    if not transcript.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="transcript is required"
        )

    raw = await audio.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty audio upload"
        )

    try:
        wav_bytes, duration = await normalize_voice_sample(raw, audio.filename or "sample")
    except ffmpeg.FFmpegError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"could not decode audio sample: {exc}",
        ) from exc
    if duration < VOICE_SAMPLE_MIN_S or duration > VOICE_SAMPLE_MAX_S:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"voice sample must be {VOICE_SAMPLE_MIN_S:.0f}–{VOICE_SAMPLE_MAX_S:.0f}s "
                f"(got {duration:.1f}s)"
            ),
        )

    # Store account-level (one sample per user; overwrites any prior upload).
    key = f"voice-samples/{user_id}/sample.wav"
    await get_storage().put(key, wav_bytes, content_type="audio/wav")

    sample = {
        "audio_key": key,
        "transcript": transcript.strip(),
        "language": (language.strip() or None),
    }
    await UserSettingsRepo(db).set_voice_sample(user_id, sample)

    return _voice_sample_status(sample, duration_s=round(duration, 2))


__all__ = ["router"]
