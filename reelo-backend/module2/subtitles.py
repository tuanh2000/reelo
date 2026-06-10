"""Auto-timed SRT generation (`subtitles.py`, M2-2).

Captions are NOT burned in — we ship ``subs.srt`` next to ``final.mp4`` so the
user (or YouTube) loads it as a sidecar track. Timing reuses the same
word-fraction clock as the renderer (:mod:`module2.timing`): each segment gets a
slice of the total audio duration proportional to its word count, then the slice
is subdivided per sentence so each cue is short enough to read. ``language`` is
carried for downstream tooling (SRT itself has no language tag).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from module2.timing import compute_timings, count_words, split_sentences


@dataclass(frozen=True)
class Cue:
    """One SRT cue: 1-based index, start/end seconds, text."""

    index: int
    start: float
    end: float
    text: str


def _fmt_ts(seconds: float) -> str:
    """Format seconds as an SRT timestamp ``HH:MM:SS,mmm``."""
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    h, ms_total = divmod(ms_total, 3_600_000)
    m, ms_total = divmod(ms_total, 60_000)
    s, ms = divmod(ms_total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_cues(narrations: list[str], audio_duration: float) -> list[Cue]:
    """Build SRT cues from narration blocks + total audio duration.

    Per segment: the segment's time slice is split among its sentences in
    proportion to their word counts, so longer sentences hold longer.
    """
    timings = compute_timings(narrations, audio_duration)
    cues: list[Cue] = []
    idx = 1
    for (seg_start, seg_end), narration in zip(timings, narrations):
        sentences = split_sentences(narration)
        if not sentences:
            continue
        span = max(0.0, seg_end - seg_start)
        wcs = [count_words(s) for s in sentences]
        total = sum(wcs) or 1
        cursor = seg_start
        for j, (sent, wc) in enumerate(zip(sentences, wcs)):
            if j == len(sentences) - 1:
                end = seg_end  # pin last sentence to the segment end (no drift)
            else:
                end = cursor + span * (wc / total)
            cues.append(Cue(index=idx, start=cursor, end=max(end, cursor), text=sent))
            cursor = end
            idx += 1
    return cues


def render_srt(cues: list[Cue]) -> str:
    """Serialize cues to SRT text (LF newlines, trailing blank line per block)."""
    blocks: list[str] = []
    for cue in cues:
        blocks.append(
            f"{cue.index}\n"
            f"{_fmt_ts(cue.start)} --> {_fmt_ts(cue.end)}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks)


def write_srt(
    narrations: list[str], audio_duration: float, out_path: Path
) -> Path:
    """Build + write ``subs.srt`` for the given narrations. Returns ``out_path``."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cues = build_cues(narrations, audio_duration)
    out_path.write_text(render_srt(cues), encoding="utf-8")
    return out_path


__all__ = ["Cue", "build_cues", "render_srt", "write_srt"]
