"""Voice orchestration — chunked TTS + concat → ``voice/voice.mp3`` (M2-6).

Long scripts (a 25-minute episode) exceed a provider's per-call character limit,
so narration sections are greedily packed into chunks under ``char_limit``, each
chunk is synthesized **sequentially** (to keep prosody/voice consistent), and the
parts are concatenated with the ffmpeg concat demuxer. The final ``voice.mp3``
duration (ffprobe) drives image timing — chunking is transparent to timing
because timing uses word-fraction of the *total* duration (±1-3s drift accepted,
integration §7 risk #3/#5).

Provider is resolved via Module 3's registry (``eleven`` | ``edge`` | ``stub-voice``);
``char_limit`` comes from the provider's ``services.yaml`` block (falls back to a
safe default). The ``===`` markers are stripped from chunk text (the voice should
not read them); the voice client also strips them defensively.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from clients.base import AIClient, CallContext, Task, VoiceRequest
from clients.registry import ServiceRegistry, get_registry
from models.spec import SeriesSpec, VoiceSample
from storage import get_storage

from module2 import ffmpeg
from module2.materialize import ProjectLayout

log = logging.getLogger("reelo.module2.voice")

DEFAULT_CHAR_LIMIT = 4000  # conservative when a provider declares none


def read_sections(script_md: Path) -> list[str]:
    """Split ``script.md`` into narration blocks by lone ``===`` lines."""
    text = script_md.read_text(encoding="utf-8")
    sections: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == "===":
            block = "\n".join(current).strip()
            if block:
                sections.append(block)
            current = []
        else:
            current.append(line)
    block = "\n".join(current).strip()
    if block:
        sections.append(block)
    return sections


def split_by_char_limit(sections: list[str], limit: int) -> list[str]:
    """Greedily pack ``sections`` into chunks each ≤ ``limit`` characters.

    A single section longer than ``limit`` becomes its own chunk (never split
    mid-sentence — TTS handles an over-limit chunk better than a cut sentence;
    providers also tolerate slight overage). Joined with blank lines so prosody
    has a sentence boundary between sections.

    Args:
        sections: narration blocks (``read_sections`` output).
        limit: per-chunk character ceiling (> 0).

    Returns:
        Non-empty chunk strings, in order.
    """
    if limit <= 0:
        limit = DEFAULT_CHAR_LIMIT
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    sep_len = 2  # the "\n\n" we join with
    for sec in sections:
        sec = sec.strip()
        if not sec:
            continue
        add_len = len(sec) + (sep_len if buf else 0)
        if buf and buf_len + add_len > limit:
            chunks.append("\n\n".join(buf))
            buf = [sec]
            buf_len = len(sec)
        else:
            buf.append(sec)
            buf_len += add_len
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def resolve_char_limit(client: AIClient) -> int:
    """Read a voice client's ``char_limit`` from its config (default if absent)."""
    block = client.config.tasks.get(Task.GENERATE_VOICE.value, {}) or {}
    limit = block.get("char_limit")
    try:
        return int(limit) if limit else DEFAULT_CHAR_LIMIT
    except (TypeError, ValueError):
        return DEFAULT_CHAR_LIMIT


@dataclass
class VoiceOutcome:
    """Result of a voice run: final mp3, its duration, and the part files."""

    voice_mp3: Path
    duration_s: float
    parts: list[Path]
    total_chars: int


def _is_clone(series: SeriesSpec) -> bool:
    """True when the series voice is in OmniVoice zero-shot clone mode."""
    return series.voice.mode == "clone" and series.voice.voice_sample is not None


async def _prepare_clone(series: SeriesSpec, lo: ProjectLayout) -> dict:
    """Download the series voice sample to a local wav and return clone args.

    Returns ``{"ref_audio": Path, "ref_text": str, "language": str|None}``. The
    sample's language falls back to the series language when unset. Raises
    ``ValueError`` if clone mode is selected without a usable sample.
    """
    sample: VoiceSample | None = series.voice.voice_sample
    if sample is None or not sample.audio_key:
        raise ValueError("voice mode 'clone' requires an uploaded voice_sample")
    ref_audio = lo.voice_dir / "ref_sample.wav"
    ref_audio.parent.mkdir(parents=True, exist_ok=True)
    await get_storage().get_to_file(sample.audio_key, ref_audio)
    return {
        "ref_audio": ref_audio,
        "ref_text": sample.transcript,
        "language": sample.language or series.language,
    }


async def synth_voice(
    series: SeriesSpec,
    lo: ProjectLayout,
    ctx: CallContext,
    *,
    registry: ServiceRegistry | None = None,
) -> VoiceOutcome:
    """Synthesize narration → ``voice/voice.mp3`` (chunk + concat) and probe duration.

    Args:
        series: provides ``voice`` (provider/voice_id/settings) + ``language``.
        lo: project layout (reads ``script.md``, writes under ``voice/``).
        ctx: per-user call context (BYOK key + usage).
        registry: override the process registry (tests).

    Returns:
        A :class:`VoiceOutcome`.

    Raises:
        ValueError: if ``script.md`` has no narration.
    """
    reg = registry or get_registry()
    client = await reg.resolve(Task.GENERATE_VOICE, series.voice.provider, ctx)

    sections = read_sections(lo.script_md)
    if not sections:
        raise ValueError("script.md has no narration sections")

    limit = resolve_char_limit(client)
    chunks = split_by_char_limit(sections, limit)

    # Carry language so keyless edge-tts can resolve its default voice.
    settings = dict(series.voice.settings or {})
    settings.setdefault("language", series.language)

    # Voice-clone mode (OmniVoice): fetch the uploaded reference sample once and
    # thread ref_audio/ref_text/language into every chunk. Preset providers
    # (edge/eleven) leave these None and behave exactly as before.
    clone = await _prepare_clone(series, lo) if _is_clone(series) else None

    parts: list[Path] = []
    total_chars = 0
    for i, text in enumerate(chunks, start=1):
        part = lo.voice_dir / f"voice_part_{i:02d}.mp3"
        req = VoiceRequest(voice_id=series.voice.voice_id, text=text, settings=settings)
        if clone is not None:
            req.ref_audio = clone["ref_audio"]
            req.ref_text = clone["ref_text"]
            req.language = clone["language"]
        await client.generate_voice(req, out_path=part, ctx=ctx)
        parts.append(part)
        total_chars += len(text)

    if len(parts) == 1:
        # Single chunk: copy/rename to voice.mp3 (still re-encode for uniformity).
        await ffmpeg.concat_audio(parts, lo.voice_mp3)
    else:
        await ffmpeg.concat_audio(parts, lo.voice_mp3)

    duration = await ffmpeg.probe_duration(lo.voice_mp3)
    log.info(
        "synth_voice: %d chunk(s), %d chars, %.1fs -> %s",
        len(parts),
        total_chars,
        duration,
        lo.voice_mp3,
    )
    return VoiceOutcome(
        voice_mp3=lo.voice_mp3,
        duration_s=duration,
        parts=parts,
        total_chars=total_chars,
    )


__all__ = [
    "DEFAULT_CHAR_LIMIT",
    "read_sections",
    "split_by_char_limit",
    "resolve_char_limit",
    "VoiceOutcome",
    "synth_voice",
]


# Re-exported helpers (used by tests / clone wiring).
__all__ += ["_is_clone", "_prepare_clone"]
