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
import re
from collections.abc import Awaitable, Callable
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


def resolve_word_limit(client: AIClient) -> int | None:
    """Read a voice client's ``word_limit`` (e.g. OmniVoice), or ``None`` if unset.

    A word limit takes precedence over ``char_limit`` and produces much smaller
    chunks. OmniVoice runs on a local GPU and is failure-prone on long inputs, so
    capping each TTS call to a few-hundred words makes synthesis far more reliable
    (and, with chunk-level resume, a failed chunk only re-does that chunk).
    """
    block = client.config.tasks.get(Task.GENERATE_VOICE.value, {}) or {}
    raw = block.get("word_limit")
    try:
        val = int(raw) if raw else 0
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def count_words(text: str) -> int:
    """Whitespace-delimited token count (a good proxy for Vietnamese + English)."""
    return len(text.split())


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on ``. ! ? …`` boundaries (keeps the punctuation)."""
    return [s for s in (p.strip() for p in _SENTENCE_BOUNDARY.split(text)) if s]


def split_by_word_limit(sections: list[str], limit: int) -> list[str]:
    """Greedily pack ``sections`` into chunks each ≤ ``limit`` WORDS.

    Unlike :func:`split_by_char_limit`, a section longer than ``limit`` IS broken
    up — by sentence — so no chunk blows past the word cap (important for a fragile
    local model). A single sentence longer than ``limit`` is still kept whole (never
    split mid-sentence). Pieces are joined with blank lines for prosody.
    """
    if limit <= 0:
        return split_by_char_limit(sections, DEFAULT_CHAR_LIMIT)
    # 1. Break oversized sections into ≤ limit-word pieces (by sentence).
    pieces: list[str] = []
    for sec in (s.strip() for s in sections):
        if not sec:
            continue
        if count_words(sec) <= limit:
            pieces.append(sec)
            continue
        buf: list[str] = []
        buf_w = 0
        for sent in split_sentences(sec):
            w = count_words(sent)
            if buf and buf_w + w > limit:
                pieces.append(" ".join(buf))
                buf, buf_w = [sent], w
            else:
                buf.append(sent)
                buf_w += w
        if buf:
            pieces.append(" ".join(buf))
    # 2. Greedily pack consecutive pieces into chunks ≤ limit words.
    chunks: list[str] = []
    buf2: list[str] = []
    buf2_w = 0
    for p in pieces:
        w = count_words(p)
        if buf2 and buf2_w + w > limit:
            chunks.append("\n\n".join(buf2))
            buf2, buf2_w = [p], w
        else:
            buf2.append(p)
            buf2_w += w
    if buf2:
        chunks.append("\n\n".join(buf2))
    return chunks


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
    on_chunk_reuse: Callable[[int, str, Path], Awaitable[bool]] | None = None,
    on_chunk_done: Callable[[int, str, Path], Awaitable[None]] | None = None,
    on_progress: Callable[[float], Awaitable[None]] | None = None,
) -> VoiceOutcome:
    """Synthesize narration → ``voice/voice.mp3`` (chunk + concat) and probe duration.

    Chunking: a provider ``word_limit`` (OmniVoice) wins over ``char_limit`` and
    yields small, reliable chunks; otherwise sections are packed by ``char_limit``.

    Resume hooks (optional — injected by the runner so this stays DB/storage-free):

    - ``on_chunk_reuse(index, chunk_text, part)`` → ``True`` if the chunk's cached
      mp3 was fetched into ``part`` (skip the TTS call); ``False`` to synthesize.
    - ``on_chunk_done(index, chunk_text, part)`` → persist a freshly-synthesized
      chunk (upload + record its hash) so a later run can reuse it.
    - ``on_progress(fraction)`` → 0.0–1.0 after each chunk, so the worker can
      advance the Voiceover job's progress bar per chunk.

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

    word_limit = resolve_word_limit(client)
    if word_limit:
        chunks = split_by_word_limit(sections, word_limit)
    else:
        chunks = split_by_char_limit(sections, resolve_char_limit(client))

    # Carry language so keyless edge-tts can resolve its default voice.
    settings = dict(series.voice.settings or {})
    settings.setdefault("language", series.language)

    # Voice-clone mode (OmniVoice): fetch the uploaded reference sample once and
    # thread ref_audio/ref_text/language into every chunk. Preset providers
    # (edge/eleven) leave these None and behave exactly as before.
    clone = await _prepare_clone(series, lo) if _is_clone(series) else None

    # Count the concat as one extra progress unit so the bar lands near (not at)
    # 1.0 just before the join.
    units = len(chunks) + 1
    parts: list[Path] = []
    total_chars = 0
    for i, text in enumerate(chunks, start=1):
        part = lo.voice_dir / f"voice_part_{i:02d}.mp3"
        # Resume: reuse this chunk's cached mp3 (unchanged + in storage) — no TTS.
        reused = False
        if on_chunk_reuse is not None:
            reused = await on_chunk_reuse(i, text, part)
        if not reused:
            req = VoiceRequest(voice_id=series.voice.voice_id, text=text, settings=settings)
            if clone is not None:
                req.ref_audio = clone["ref_audio"]
                req.ref_text = clone["ref_text"]
                req.language = clone["language"]
            await client.generate_voice(req, out_path=part, ctx=ctx)
            total_chars += len(text)
            if on_chunk_done is not None:
                await on_chunk_done(i, text, part)
        parts.append(part)
        if on_progress is not None:
            await on_progress(min(i / units, 1.0))

    # Concatenate the per-chunk parts (re-encode) → voice.mp3 (uniform, clean joins).
    await ffmpeg.concat_audio(parts, lo.voice_mp3)
    if on_progress is not None:
        await on_progress(1.0)

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
    "split_by_word_limit",
    "resolve_char_limit",
    "resolve_word_limit",
    "count_words",
    "split_sentences",
    "VoiceOutcome",
    "synth_voice",
]


# Re-exported helpers (used by tests / clone wiring).
__all__ += ["_is_clone", "_prepare_clone"]
