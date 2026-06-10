"""Stub clients — produce valid outputs with no external API (Module 3).

These let Module 1 and Module 2 run their pipelines end-to-end in tests / local
dev without real provider keys or network. They are registered via
``services.yaml`` under provider ids ``stub-script`` / ``stub-voice`` /
``stub-image`` (kept out of the production ``routing.fallback`` so they never
leak into a real run unless explicitly selected).

- :class:`EchoScriptClient` — returns a :class:`ScriptResult` whose ``text`` is
  valid JSON honouring the request's ``json_schema`` (so Module 1's structured
  parse succeeds), plus a fake token usage block.
- :class:`SilentVoiceClient` — writes a **real, decodable** silent MP3 to
  ``out_path``. It prefers ffmpeg (``anullsrc`` → libmp3lame) so the bytes are
  byte-for-byte what a real provider would emit; if ffmpeg is unavailable it
  falls back to repeating one verified-valid MPEG-1 Layer III frame. Either way
  the file passes ``ffprobe`` and survives the renderer's concat/re-encode, so
  the full-stub tracer-bullet runs end-to-end through ffmpeg 8.x.
- :class:`PlaceholderImageClient` — writes a small solid-colour PNG (built with
  stdlib ``zlib`` only, no Pillow) to ``out_path``.

All three are keyless and always available.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Any

from clients.base import (
    AIClient,
    CallContext,
    ImageRequest,
    ImageResult,
    ScriptRequest,
    ScriptResult,
    Task,
    VoiceRequest,
    VoiceResult,
)

# One verified-valid steady-state silent MPEG-1 Layer III frame: 44.1 kHz,
# 32 kbps, mono, 105 bytes, 1152 samples (~26.12 ms). Generated with
# ``ffmpeg -f lavfi -i anullsrc=r=44100:cl=mono -c:a libmp3lame -b:a 32k`` and
# taken from the middle of the stream (a pure-silence frame, not the leading
# LAME-info frame). Starts with the 0xFF 0xFB frame sync (no ID3 tag) and a
# repetition of it decodes in ffprobe without "two consecutive frames" errors.
_SILENT_MP3_FRAME = bytes.fromhex(
    "fffb12c4d083c00001a4000000200000348000000455555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555555554c414d45332e31303055"  # noqa: E501
)
_FRAME_SECONDS = 1152 / 44100  # ~0.02612 s per frame

# Speaking-rate model so the silent track length tracks the text (keeps image
# timing meaningful in the renderer). ~15 chars/sec ≈ a calm narration pace.
_CHARS_PER_SECOND = 15.0
_MIN_SECONDS = 1.0
_MAX_SECONDS = 1800.0  # safety cap (30 min)


def _text_duration_seconds(text: str) -> float:
    n = len(text.strip())
    secs = max(_MIN_SECONDS, n / _CHARS_PER_SECOND) if n else _MIN_SECONDS
    return min(secs, _MAX_SECONDS)


def _silent_mp3_bytes(seconds: float) -> bytes:
    """A decodable silent MP3 of ~``seconds`` length, by repeating one frame."""
    frames = max(1, round(seconds / _FRAME_SECONDS))
    return _SILENT_MP3_FRAME * frames


def _ffmpeg_bin() -> str | None:
    import os

    cand = os.environ.get("REELO_FFMPEG") or "ffmpeg"
    if shutil.which(cand) or Path(cand).exists():
        return cand
    return None


def _write_silent_mp3_ffmpeg(out_path: Path, seconds: float, ffmpeg: str) -> bool:
    """Write a real silent MP3 via ffmpeg. Returns True on success."""
    argv = [
        ffmpeg, "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=mono",
        "-t", f"{seconds:.3f}",
        "-c:a", "libmp3lame",
        "-b:a", "64k",
        "-id3v2_version", "0",  # no ID3 tag → file starts with frame sync
        "-write_xing", "0",
        str(out_path),
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Build a solid-colour PNG using only the stdlib (no Pillow)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    row = b"\x00" + bytes(rgb) * width  # filter byte 0 + pixels
    raw = row * height
    idat = zlib.compress(raw, 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _sample_for_schema(schema: dict[str, Any]) -> Any:
    """Produce a minimal value satisfying a (subset of) JSON Schema.

    Supports object/array/string/number/integer/boolean and ``enum``. Unknown
    shapes fall back to an empty string. Good enough for Module 1's segment
    schema so the structured-output parse path exercises real data.
    """
    if not isinstance(schema, dict):
        return ""
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    stype = schema.get("type")
    if stype == "object":
        props = schema.get("properties", {}) or {}
        return {k: _sample_for_schema(v) for k, v in props.items()}
    if stype == "array":
        items = schema.get("items", {}) or {}
        # one element so consumers see non-empty arrays
        return [_sample_for_schema(items)]
    if stype in ("number", "integer"):
        return 0
    if stype == "boolean":
        return False
    # default + string
    return schema.get("default", "stub")


# Module 1's chunk user message says e.g. "Write segments index 3..7 (5 segment(s))"
# and "Output exactly 5 segment(s), with index running ...". The stub parses the
# count + start index so its segments array validates against ``validate_chunk``.
_INDEX_RANGE_RE = re.compile(r"index\s+(\d+)\s*\.\.\s*(\d+)")
_EXACT_COUNT_RE = re.compile(r"exactly\s+(\d+)\s+segment")


def _is_segments_schema(schema: dict[str, Any]) -> bool:
    """True iff this looks like Module 1's per-chunk segments schema."""
    props = (schema or {}).get("properties", {}) or {}
    seg = props.get("segments")
    if not isinstance(seg, dict) or seg.get("type") != "array":
        return False
    item_props = (seg.get("items", {}) or {}).get("properties", {}) or {}
    # The Module-1 schema has these four fields; the generic test schema lacks
    # ``image_label`` so it falls through to the plain sampler.
    return {"index", "narration", "image_prompt", "image_label"} <= set(item_props)


def _segments_payload(idx_start: int, count: int) -> dict[str, Any]:
    """Build a schema-valid ``{"segments": [...]}`` block of ``count`` items."""
    segments = []
    for i in range(idx_start, idx_start + count):
        segments.append(
            {
                "index": i,
                "narration": (
                    f"Stub narration for segment {i}. This is placeholder spoken "
                    "text long enough to drive timing and subtitles in a tracer run."
                ),
                "image_prompt": f"placeholder watercolor scene number {i}, plain background",
                "image_label": f"scene-{i:03d}",
                "image_query": f"placeholder scene {i}",
            }
        )
    return {"segments": segments}


class EchoScriptClient(AIClient):
    """Returns deterministic, schema-valid JSON for WRITE_SCRIPT (no API)."""

    capabilities = {Task.WRITE_SCRIPT}
    cost_tier = "free"
    requires_key = False

    async def is_available(self, ctx: CallContext) -> bool:
        return True

    async def validate_key(self, ctx: CallContext) -> bool:
        return True

    async def write_script(self, req: ScriptRequest, ctx: CallContext) -> ScriptResult:
        if req.json_schema and _is_segments_schema(req.json_schema):
            # Lazy-script chunk: honour the requested index range / count so the
            # output validates (Module 1 ``validate_chunk``).
            last = req.messages[-1]["content"] if req.messages else ""
            rng = _INDEX_RANGE_RE.search(last)
            cnt = _EXACT_COUNT_RE.search(last)
            if rng:
                idx_start, idx_end = int(rng.group(1)), int(rng.group(2))
                count = idx_end - idx_start + 1
            elif cnt:
                idx_start, count = 1, int(cnt.group(1))
            else:
                idx_start, count = 1, 1
            count = max(1, count)
            text = json.dumps(_segments_payload(idx_start, count), ensure_ascii=False)
        elif req.json_schema:
            payload = _sample_for_schema(req.json_schema)
            # Echo the latest user turn into the first string-ish field if present.
            text = json.dumps(payload, ensure_ascii=False)
        else:
            last = req.messages[-1]["content"] if req.messages else ""
            text = f"[stub-script] {last}"
        model = req.model or "stub-echo"
        usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        ctx.usage.record(ctx.user_id, self.provider_id, Task.WRITE_SCRIPT.value, 30.0, 0.0)
        return ScriptResult(text=text, model=model, usage=usage, raw={"stub": True})


class SilentVoiceClient(AIClient):
    """Writes a tiny valid silent MP3 for GENERATE_VOICE (no API)."""

    capabilities = {Task.GENERATE_VOICE}
    cost_tier = "free"
    requires_key = False

    async def is_available(self, ctx: CallContext) -> bool:
        return True

    async def validate_key(self, ctx: CallContext) -> bool:
        return True

    async def generate_voice(
        self, req: VoiceRequest, out_path: Path, ctx: CallContext
    ) -> VoiceResult:
        if req.text is not None:
            text = req.text
        elif req.text_file is not None:
            text = Path(req.text_file).read_text(encoding="utf-8")
        else:
            text = ""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        seconds = _text_duration_seconds(text)
        ffmpeg = _ffmpeg_bin()
        wrote_via_ffmpeg = False
        if ffmpeg is not None:
            wrote_via_ffmpeg = await asyncio.to_thread(
                _write_silent_mp3_ffmpeg, out_path, seconds, ffmpeg
            )
        if not wrote_via_ffmpeg:
            # Keyless / ffmpeg-free fallback: still a decodable MP3.
            out_path.write_bytes(_silent_mp3_bytes(seconds))

        chars = len(text)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_VOICE.value, float(chars), 0.0)
        return VoiceResult(
            out_path=out_path, duration_s=round(seconds, 2), chars=chars, raw={"stub": True}
        )


class PlaceholderImageClient(AIClient):
    """Writes a small solid-colour PNG for GENERATE_IMAGE (no API)."""

    capabilities = {Task.GENERATE_IMAGE}
    cost_tier = "free"
    requires_key = False

    # aspect-ratio -> (w, h) small placeholder dims
    _DIMS: dict[str, tuple[int, int]] = {
        "16:9": (64, 36),
        "9:16": (36, 64),
        "4:3": (64, 48),
        "3:4": (48, 64),
        "1:1": (48, 48),
    }

    async def is_available(self, ctx: CallContext) -> bool:
        return True

    async def validate_key(self, ctx: CallContext) -> bool:
        return True

    async def generate_image(
        self, req: ImageRequest, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        w, h = self._DIMS.get(req.size, (64, 36))
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(_solid_png(w, h, (40, 70, 120)))  # navy-ish
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_IMAGE.value, 1.0, 0.0)
        return ImageResult(out_path=out_path, count=1, raw={"stub": True})


__all__ = ["EchoScriptClient", "SilentVoiceClient", "PlaceholderImageClient"]
