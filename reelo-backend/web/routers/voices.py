"""Shared OmniVoice voice-clone library ("Giọng clone đã tạo").

OmniVoice clones a voice from a reference clip + its transcript + language. Rather
than make every user re-enter that each time, a user can NAME their reference and
save it here; the catalog is then **shared across all tenants** so anyone can
reuse a voice a previous user created (the product requirement).

This router owns the library itself; applying a chosen voice to a series lives in
``web.routers.series`` (``POST /series/{id}/voice/custom``) next to the other
series-voice mutations.

Endpoints:
- ``GET /voices``               — browse the shared catalog (any logged-in user).
- ``POST /voices``              — create a named voice from an upload (multipart).
- ``GET /voices/{id}/preview``  — a signed URL to listen to the reference clip.
- ``DELETE /voices/{id}``       — delete (creator only).

The reference clip is normalized exactly like the per-series voice sample (wav
24 kHz mono, 3–30 s) and stored under a GLOBAL prefix so it stays readable when a
different tenant produces with it. Privacy: the clip is the user's real voice and
is exposed to every tenant by design — never log the bytes or transcript.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from db.models import CustomVoice
from db.repository import CustomVoiceRepo
from module2 import ffmpeg
from storage import get_storage
from web._voice_sample import (
    VOICE_SAMPLE_MAX_S,
    VOICE_SAMPLE_MIN_S,
    normalize_voice_sample,
)
from web.deps import CurrentUser, DbSession
from web.schemas import (
    CustomVoiceItem,
    CustomVoiceListResponse,
    CustomVoicePreviewResponse,
)

router = APIRouter(prefix="/voices", tags=["voices"])


def custom_voice_audio_key(voice_id: str) -> str:
    """Global (not per-tenant) storage key for a library voice's reference clip."""
    return f"custom-voices/{voice_id}/sample.wav"


def _to_item(row: CustomVoice, *, user_id: str) -> CustomVoiceItem:
    return CustomVoiceItem(
        id=row.id,
        name=row.name,
        language=row.language,
        transcript=row.transcript,
        duration_s=row.duration_s,
        is_owner=row.created_by_user_id == user_id,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


@router.get("", response_model=CustomVoiceListResponse)
async def list_voices(user_id: CurrentUser, db: DbSession) -> CustomVoiceListResponse:
    """Browse the shared voice-clone library (every tenant's voices, newest first)."""
    rows = await CustomVoiceRepo(db).list_all()
    return CustomVoiceListResponse(voices=[_to_item(r, user_id=user_id) for r in rows])


@router.post("", response_model=CustomVoiceItem, status_code=status.HTTP_201_CREATED)
async def create_voice(
    user_id: CurrentUser,
    db: DbSession,
    audio: UploadFile = File(...),
    name: str = Form(...),
    transcript: str = Form(...),
    language: str = Form(default=""),
) -> CustomVoiceItem:
    """Create a named, reusable voice from a reference upload (multipart).

    Validates + normalizes the clip to wav 24 kHz mono (3–30 s), stores it under a
    global ``custom-voices/`` prefix, and records a shared library row. The result
    is immediately visible to every user via ``GET /voices``. Apply it to a series
    with ``POST /series/{id}/voice/custom``.
    """
    if not name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="name is required"
        )
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

    voice_id = f"voice_{uuid.uuid4().hex[:12]}"
    audio_key = custom_voice_audio_key(voice_id)
    await get_storage().put(audio_key, wav_bytes, content_type="audio/wav")

    row = await CustomVoiceRepo(db).create(
        voice_id=voice_id,
        created_by_user_id=user_id,
        name=name.strip(),
        audio_key=audio_key,
        transcript=transcript.strip(),
        language=language.strip() or None,
        duration_s=round(duration, 2),
    )
    return _to_item(row, user_id=user_id)


@router.get("/{voice_id}/preview", response_model=CustomVoicePreviewResponse)
async def preview_voice(
    voice_id: str, user_id: CurrentUser, db: DbSession
) -> CustomVoicePreviewResponse:
    """Return a time-limited URL to listen to a library voice's reference clip."""
    row = await CustomVoiceRepo(db).get(voice_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"voice {voice_id} not found"
        )
    url = await get_storage().signed_url(row.audio_key)
    return CustomVoicePreviewResponse(url=url)


@router.delete("/{voice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_voice(voice_id: str, user_id: CurrentUser, db: DbSession) -> None:
    """Delete a library voice. Only the creator may delete (403 otherwise).

    Series that already snapshotted this voice keep working — they reference the
    same audio object, which we also remove here, but a clone reads from the
    series' own ``voice_sample.audio_key`` snapshot. (Snapshots point at the same
    global key, so a delete does remove the shared clip; that is the documented
    trade-off of a shared library — deleting a voice others adopted breaks their
    next produce. We keep deletes creator-only to bound the blast radius.)
    """
    repo = CustomVoiceRepo(db)
    row = await repo.get(voice_id)
    audio_key = row.audio_key if row is not None else None
    outcome = await repo.delete_owned(voice_id, user_id)
    if outcome == "missing":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"voice {voice_id} not found"
        )
    if outcome == "forbidden":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only the creator can delete this voice",
        )
    if audio_key:
        await get_storage().delete(audio_key)


__all__ = ["router", "custom_voice_audio_key"]
