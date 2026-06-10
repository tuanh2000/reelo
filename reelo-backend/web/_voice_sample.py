"""Shared helpers for voice-clone reference uploads (OmniVoice).

A voice-clone reference must be a short, clean clip. Both the account-level
endpoint (``POST /settings/voice-sample``) and the per-series override
(``POST /series/{id}/voice-sample``) normalize the upload the same way: transcode
to wav 24 kHz mono via ffmpeg and validate the duration is within bounds.

Never log the audio bytes or the transcript (privacy: it is the user's voice).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from module2 import ffmpeg

# Voice-clone sample bounds (seconds). OmniVoice clones best from a short, clean
# clip; reject too-short (no voiceprint) or too-long (wasteful / off-task) uploads.
VOICE_SAMPLE_MIN_S = 3.0
VOICE_SAMPLE_MAX_S = 30.0


async def normalize_voice_sample(raw: bytes, name: str) -> tuple[bytes, float]:
    """Transcode an uploaded clip to wav 24 kHz mono; return (wav_bytes, duration).

    Writes the upload to a temp file, runs ffmpeg (``-ar 24000 -ac 1``), probes
    the result's duration, and returns the normalized bytes. Raises
    :class:`module2.ffmpeg.FFmpegError` if the source cannot be decoded.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="reelo_vsample_"))
    src = tmp_dir / ("src_" + Path(name).name)
    out = tmp_dir / "sample.wav"
    try:
        src.write_bytes(raw)
        argv = [
            ffmpeg.ffmpeg_bin(),
            "-y",
            "-i",
            str(src),
            "-ar",
            "24000",
            "-ac",
            "1",
            str(out),
        ]
        await ffmpeg.run(argv, timeout=120)
        duration = await ffmpeg.probe_duration(out)
        return out.read_bytes(), duration
    finally:
        for p in (src, out):
            p.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


__all__ = [
    "VOICE_SAMPLE_MIN_S",
    "VOICE_SAMPLE_MAX_S",
    "normalize_voice_sample",
]
