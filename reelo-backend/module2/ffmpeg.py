"""FFmpeg / ffprobe seams shared by the renderer and the voice orchestrator.

Design rule (Module 2 charter): **building** an ffmpeg argument list is always a
pure function (no subprocess), so it can be unit-tested by comparing the exact
argv. **Running** it is a thin async wrapper around :func:`asyncio.create_subprocess_exec`.
This keeps the filtergraph logic verifiable without ffmpeg installed.

``ffmpeg`` / ``ffprobe`` are resolved from PATH (overridable via env
``REELO_FFMPEG`` / ``REELO_FFPROBE`` for pinned installs). The renderer does NOT
use the skill subprocess helper for ffmpeg — that helper parses JSON stdout and
targets the skill scripts; ffmpeg/ffprobe have their own argv + parsing here.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path


class FFmpegError(RuntimeError):
    """An ffmpeg / ffprobe invocation exited non-zero. Carries trimmed stderr."""

    def __init__(self, argv: list[str], returncode: int, stderr: str) -> None:
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        tail = stderr.strip()[-1000:]
        super().__init__(f"{argv[0]} exited {returncode}: {tail}")


def ffmpeg_bin() -> str:
    """Path to the ffmpeg binary (env ``REELO_FFMPEG`` or PATH)."""
    return os.environ.get("REELO_FFMPEG") or "ffmpeg"


def ffprobe_bin() -> str:
    """Path to the ffprobe binary (env ``REELO_FFPROBE`` or PATH)."""
    return os.environ.get("REELO_FFPROBE") or "ffprobe"


def ffmpeg_available() -> bool:
    """True iff both ffmpeg and ffprobe resolve (used to skip smoke tests)."""
    ff = ffmpeg_bin()
    fp = ffprobe_bin()
    return (shutil.which(ff) is not None or Path(ff).exists()) and (
        shutil.which(fp) is not None or Path(fp).exists()
    )


# --------------------------------------------------------------------------- #
# Pure build-command helpers (unit-testable)                                  #
# --------------------------------------------------------------------------- #
def build_probe_duration_cmd(media_path: Path) -> list[str]:
    """ffprobe argv that prints a media file's duration (seconds) to stdout."""
    return [
        ffprobe_bin(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]


def build_probe_dimensions_cmd(image_path: Path) -> list[str]:
    """ffprobe argv that prints ``W,H`` for the first video stream of an image."""
    return [
        ffprobe_bin(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0",
        str(image_path),
    ]


def build_concat_cmd(list_file: Path, out_path: Path) -> list[str]:
    """ffmpeg argv that concatenates audio via the concat demuxer (re-encode).

    Re-encoding (rather than ``-c copy``) is deliberate: TTS chunks can carry
    slightly different headers; re-encoding to a uniform MP3 guarantees a clean
    join with no audible gaps/clicks.
    """
    return [
        ffmpeg_bin(),
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(out_path),
    ]


# --------------------------------------------------------------------------- #
# Exec wrappers                                                               #
# --------------------------------------------------------------------------- #
async def run(argv: list[str], *, timeout: float | None = None) -> str:
    """Run an ffmpeg/ffprobe argv; return stdout. Raise :class:`FFmpegError` on failure."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise FFmpegError(argv, -1, f"timed out after {timeout}s") from exc
    out = out_b.decode("utf-8", errors="replace")
    err = err_b.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(argv, proc.returncode or -1, err)
    return out


async def probe_duration(media_path: Path, *, timeout: float | None = 60) -> float:
    """Return a media file's duration in seconds (ffprobe)."""
    out = await run(build_probe_duration_cmd(Path(media_path)), timeout=timeout)
    text = out.strip()
    try:
        return float(text)
    except ValueError as exc:
        raise FFmpegError(
            build_probe_duration_cmd(Path(media_path)), 0, f"unparseable duration: {text!r}"
        ) from exc


async def probe_dimensions(
    image_path: Path, *, timeout: float | None = 60
) -> tuple[int, int]:
    """Return ``(width, height)`` of an image (ffprobe)."""
    out = await run(build_probe_dimensions_cmd(Path(image_path)), timeout=timeout)
    parts = out.strip().split(",")
    if len(parts) < 2:
        raise FFmpegError(
            build_probe_dimensions_cmd(Path(image_path)), 0, f"unparseable dims: {out!r}"
        )
    return int(parts[0]), int(parts[1])


async def concat_audio(
    parts: list[Path], out_path: Path, *, timeout: float | None = 600
) -> Path:
    """Concatenate MP3 ``parts`` into ``out_path`` via the concat demuxer.

    Writes a temp concat list file next to the output, runs ffmpeg, then removes
    the list. Returns ``out_path``.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = out_path.with_suffix(".concat.txt")
    # The concat demuxer needs single-quoted paths with embedded quotes escaped.
    lines = [f"file '{_escape_concat_path(Path(p).resolve())}'" for p in parts]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        await run(build_concat_cmd(list_file, out_path), timeout=timeout)
    finally:
        list_file.unlink(missing_ok=True)
    return out_path


def _escape_concat_path(path: Path) -> str:
    """Escape a path for a concat-demuxer list line (single-quote the value)."""
    return str(path).replace("'", "'\\''")


__all__ = [
    "FFmpegError",
    "ffmpeg_bin",
    "ffprobe_bin",
    "ffmpeg_available",
    "build_probe_duration_cmd",
    "build_probe_dimensions_cmd",
    "build_concat_cmd",
    "run",
    "probe_duration",
    "probe_dimensions",
    "concat_audio",
]
