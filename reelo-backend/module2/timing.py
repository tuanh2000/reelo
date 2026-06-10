"""Word-count image timing — inherited from the skill's ``merge_video.py``.

Each segment shows for ``(words_i / total_words) × audio_duration`` seconds. This
is intentionally approximate (±1-3s on a long video, integration §7 risk #3): the
audience listens to the voice while images hold for tens of seconds, so exact
cuts are not needed. The renderer and the SRT generator share this function so
images and captions stay aligned to the same clock.
"""

from __future__ import annotations

import re


def count_words(text: str) -> int:
    """Whitespace word count, robust to empty/blank text (min 1 so it never zeroes)."""
    n = len(text.split())
    return n if n > 0 else 1


def compute_timings(
    narrations: list[str], audio_duration: float
) -> list[tuple[float, float]]:
    """Map each narration block to ``(start_s, end_s)`` by word-fraction.

    The final block's end is pinned to ``audio_duration`` exactly to absorb
    float drift (so the video and audio finish together).

    Args:
        narrations: one string per segment (the ``===`` blocks of ``script.md``).
        audio_duration: total ``voice.mp3`` duration in seconds.

    Returns:
        ``[(start, end), ...]`` aligned 1:1 with ``narrations``.
    """
    if not narrations:
        return []
    word_counts = [count_words(s) for s in narrations]
    total = sum(word_counts) or 1
    timings: list[tuple[float, float]] = []
    cumulative = 0
    for wc in word_counts:
        start = cumulative / total * audio_duration
        cumulative += wc
        end = cumulative / total * audio_duration
        timings.append((start, end))
    last_start, _ = timings[-1]
    timings[-1] = (last_start, audio_duration)
    return timings


_SENTENCE_RE = re.compile(r"[^.!?。！？\n]+[.!?。！？]?", re.UNICODE)


def split_sentences(text: str) -> list[str]:
    """Split narration into sentence-ish fragments for SRT cues.

    Terminator-aware (handles Latin + CJK punctuation) and falls back to the
    whole block when no terminator is present.
    """
    parts = [m.group(0).strip() for m in _SENTENCE_RE.finditer(text)]
    parts = [p for p in parts if p]
    return parts or ([text.strip()] if text.strip() else [])


__all__ = ["count_words", "compute_timings", "split_sentences"]
