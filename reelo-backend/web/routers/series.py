"""Series CRUD (Module 1: reelo-scriptwriting) + music upload (Module 2).

Series are scoped by ``user_id``; the full :class:`models.spec.SeriesSpec` lives
in ``series.spec_json`` with episode rows mirrored for lookup/status.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from db.repository import SeriesRepo
from models.spec import VoiceSample
from module1.persistence import save_series_spec, spec_from_row
from module2 import ffmpeg
from storage import episode_key, get_storage
from web._voice_sample import (
    VOICE_SAMPLE_MAX_S,
    VOICE_SAMPLE_MIN_S,
    normalize_voice_sample,
)
from web.deps import CurrentUser, DbSession
from web.schemas import (
    MusicUploadResponse,
    RenameSeriesRequest,
    SaveSeriesRequest,
    SaveSeriesResponse,
    SeriesListResponse,
    VoiceSampleResponse,
)

router = APIRouter(prefix="/series", tags=["series"])


@router.get("", response_model=SeriesListResponse)
async def list_series(user_id: CurrentUser, db: DbSession) -> SeriesListResponse:
    """List the user's series (maps ``listSeries``)."""
    rows = await SeriesRepo(db).list_for_user(user_id)
    return SeriesListResponse(series=[spec_from_row(r) for r in rows])


@router.post("", response_model=SaveSeriesResponse)
async def create_series(
    body: SaveSeriesRequest, user_id: CurrentUser, db: DbSession
) -> SaveSeriesResponse:
    """Create / upsert a series from a full spec (maps ``saveSeries``)."""
    await save_series_spec(db, user_id, body.series)
    return SaveSeriesResponse(series=body.series)


@router.put("/{series_id}", response_model=SaveSeriesResponse)
async def update_series(
    series_id: str, body: SaveSeriesRequest, user_id: CurrentUser, db: DbSession
) -> SaveSeriesResponse:
    """Update a series (maps ``saveSeries``). The path id is authoritative."""
    existing = await SeriesRepo(db).get(user_id, series_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"series {series_id} not found"
        )
    spec = body.series.model_copy(update={"series_id": series_id})
    await save_series_spec(db, user_id, spec)
    return SaveSeriesResponse(series=spec)


@router.patch("/{series_id}", response_model=SaveSeriesResponse)
async def rename_series(
    series_id: str, body: RenameSeriesRequest, user_id: CurrentUser, db: DbSession
) -> SaveSeriesResponse:
    """Rename a series (maps ``renameSeries(id, name)``).

    Updates both the denormalized ``Series.name`` column and ``spec_json.name``
    (kept in sync by the repo). Returns the updated series in the same shape as a
    ``listSeries`` item. 404 when the series is missing / not owned by the user.
    """
    row = await SeriesRepo(db).rename(user_id, series_id, body.name)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"series {series_id} not found"
        )
    return SaveSeriesResponse(series=spec_from_row(row))


@router.post("/{series_id}/music", response_model=MusicUploadResponse)
async def upload_music(
    series_id: str,
    user_id: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
) -> MusicUploadResponse:
    """Upload optional per-series background music (multipart) → series.music.path.

    Stores the track in object storage under the series prefix and records its
    object key in ``series.spec_json.music.path`` (M2-1). The renderer downloads
    that key and ducks/loops it under the voice.
    """
    repo = SeriesRepo(db)
    row = await repo.get(user_id, series_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"series {series_id} not found"
        )

    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="empty music upload"
        )
    # Store per-series (not per-episode): projects/<user>/series_<id>/music/bg.mp3
    key = episode_key(user_id, f"series_{series_id}", "music", "bg.mp3")
    await get_storage().put(key, data, content_type=file.content_type or "audio/mpeg")

    spec = spec_from_row(row)
    music = dict(spec.music or {})
    music["path"] = key
    spec.music = music
    row.spec_json = spec.model_dump()
    await db.flush()

    return MusicUploadResponse(path=key)


@router.post("/{series_id}/voice-sample", response_model=VoiceSampleResponse)
async def upload_voice_sample(
    series_id: str,
    user_id: CurrentUser,
    db: DbSession,
    audio: UploadFile = File(...),
    transcript: str = Form(...),
    language: str = Form(default=""),
) -> VoiceSampleResponse:
    """Upload a voice-clone reference sample (multipart) → OmniVoice clone config.

    Validates + normalizes the upload to a wav 24 kHz mono clip (ffmpeg), stores
    it under ``voice-samples/<user>/<series>/sample.wav`` in object storage, and
    sets ``series.spec_json.voice`` to OmniVoice clone mode with the sample's
    object key + transcript + language (M2-14). The reference clip must be
    3–30 s of clean speech matching ``transcript``.
    """
    repo = SeriesRepo(db)
    row = await repo.get(user_id, series_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"series {series_id} not found"
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

    # Normalize to wav 24 kHz mono and measure duration (validate 3–30 s).
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

    # Store per-series under a dedicated voice-samples prefix (not per-episode).
    key = f"voice-samples/{user_id}/{series_id}/sample.wav"
    await get_storage().put(key, wav_bytes, content_type="audio/wav")

    # Flip the series voice config to OmniVoice clone mode.
    spec = spec_from_row(row)
    sample = VoiceSample(
        audio_key=key, transcript=transcript.strip(), language=language or None
    )
    spec.voice.provider = "omnivoice"
    spec.voice.mode = "clone"
    spec.voice.voice_sample = sample
    spec.providers = {**spec.providers, "voice": "omnivoice"}
    row.spec_json = spec.model_dump()
    await db.flush()

    return VoiceSampleResponse(audio_key=key, duration_s=round(duration, 2), voice=spec.voice)


__all__ = ["router"]
