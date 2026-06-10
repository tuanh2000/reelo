#!/usr/bin/env python3
"""Merge voice.mp3 + images into a final 1920x1080 MP4 via FFmpeg.

Image timing is auto-calculated:
  - Read script.md, split into sections by lines containing only "==="
  - Count words per section, get total audio duration via ffprobe on voice.mp3
  - Each section i shows for (words_in_section_i / total_words) × audio_duration

The PNGs in images/ are matched to sections by sorted filename — there must be
exactly one PNG per section.

Each image is rendered on a 1920x1080 canvas (centered, blurred sides if narrower
than 16:9). Images are fully static. Clips chained with 0.6s crossfade.
Final output: H.264 / AAC MP4 ready to upload to YouTube.

Usage:
    python merge_video.py --video-folder <path-to-video-folder>
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
CROSSFADE_DURATION = 0.6
FPS = 30


def check_ffmpeg() -> str:
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, check=True
        )
        return result.stdout.split("\n", 1)[0]
    except (subprocess.CalledProcessError, FileNotFoundError):
        raise SystemExit(
            "FFmpeg not found in PATH. Install it:\n"
            "  Windows:  winget install ffmpeg\n"
            "  macOS:    brew install ffmpeg\n"
            "  Linux:    sudo apt install ffmpeg\n"
            "Then open a new terminal and verify with:  ffmpeg -version"
        )


def get_image_dimensions(image_path: Path) -> tuple[int, int]:
    """Return (width, height) of an image via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(image_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"ffprobe failed on {image_path.name}: {e.stderr}")
    parts = result.stdout.strip().split(",")
    if len(parts) < 2:
        raise SystemExit(f"ffprobe returned unexpected output for {image_path.name}: {result.stdout!r}")
    return int(parts[0]), int(parts[1])


def compute_foreground_size(img_w: int, img_h: int, canvas_w: int = 1920, canvas_h: int = 1080) -> tuple[int, int]:
    """Scale image to fit inside the canvas while preserving aspect. Round to even pixels for H.264."""
    # Try fitting to canvas height first
    fg_h = canvas_h
    fg_w = round(img_w / img_h * fg_h / 2) * 2
    # If that overflows canvas width, refit by width instead
    if fg_w > canvas_w:
        fg_w = canvas_w
        fg_h = round(img_h / img_w * fg_w / 2) * 2
    return max(2, fg_w), max(2, fg_h)


def parse_script_sections(script_path: Path) -> list[str]:
    """Split script.md by '===' marker lines. Return list of section text blocks."""
    text = script_path.read_text(encoding="utf-8")
    sections: list[str] = []
    current: list[str] = []
    for line in text.split("\n"):
        if line.strip() == "===":
            if current:
                block = "\n".join(current).strip()
                if block:
                    sections.append(block)
            current = []
        else:
            current.append(line)
    if current:
        block = "\n".join(current).strip()
        if block:
            sections.append(block)
    return sections


def get_audio_duration(audio_path: Path) -> float:
    """Return MP3 duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(audio_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"ffprobe failed on {audio_path.name}: {e.stderr}")
    return float(result.stdout.strip())


def compute_timings(sections: list[str], audio_duration: float) -> list[tuple[float, float]]:
    """Map each section to (start_s, end_s) by word-fraction × audio_duration.

    Accuracy ~±1-3s on a typical video — good enough when images hold for tens
    of seconds and the audience is listening, not watching pixel-perfect cuts.
    """
    word_counts = [len(s.split()) for s in sections]
    total = sum(word_counts) or 1
    timings: list[tuple[float, float]] = []
    cumulative = 0
    for wc in word_counts:
        start = cumulative / total * audio_duration
        cumulative += wc
        end = cumulative / total * audio_duration
        timings.append((start, end))
    # Force the last section's end to match audio exactly (avoids float drift).
    if timings:
        last_start, _ = timings[-1]
        timings[-1] = (last_start, audio_duration)
    return timings


def build_image_clip(
    image_path: Path,
    duration: float,
    output_path: Path,
) -> None:
    """Render a single image as a static video clip of the given duration."""
    img_w, img_h = get_image_dimensions(image_path)
    fg_w, fg_h = compute_foreground_size(img_w, img_h)

    filter_complex = (
        "[0:v]scale=1920:1080:force_original_aspect_ratio=increase,"
        f"crop=1920:1080,gblur=sigma=30,eq=brightness=-0.25,fps={FPS}[bg];"
        f"[0:v]scale={fg_w}:{fg_h},fps={FPS}[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-t", f"{duration:.3f}",
        "-i", str(image_path),
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-r", str(FPS),
        "-t", f"{duration:.3f}",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise SystemExit(
            f"FFmpeg failed rendering {image_path.name}:\n{e.stderr[-2000:]}"
        )


def merge_clips_with_audio(
    clip_paths: list[Path],
    clip_durations: list[float],
    audio_path: Path,
    output_path: Path,
) -> None:
    """Chain clips with xfade crossfade and mux in the voice audio."""
    n = len(clip_paths)

    if n == 1:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(clip_paths[0]),
            "-i", str(audio_path),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"FFmpeg failed:\n{e.stderr[-2000:]}")
        return

    inputs: list[str] = []
    for clip in clip_paths:
        inputs.extend(["-i", str(clip)])
    inputs.extend(["-i", str(audio_path)])

    filter_parts: list[str] = []
    last_label = "[0:v]"
    cumulative = clip_durations[0]

    for i in range(1, n):
        offset = cumulative - CROSSFADE_DURATION
        out_label = "[vout]" if i == n - 1 else f"[v{i}]"
        filter_parts.append(
            f"{last_label}[{i}:v]xfade=transition=fade:"
            f"duration={CROSSFADE_DURATION}:offset={offset:.3f}{out_label}"
        )
        cumulative += clip_durations[i] - CROSSFADE_DURATION
        last_label = out_label

    filter_complex = ";".join(filter_parts)
    audio_input_index = n

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]",
        "-map", f"{audio_input_index}:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"FFmpeg final merge failed:\n{e.stderr[-2000:]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--video-folder",
        required=True,
        help="Per-video folder containing script.md, voice/voice.mp3, and images/*.png",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output MP4 path (default: <video-folder>/final.mp4)",
    )
    args = parser.parse_args()

    ffmpeg_version = check_ffmpeg()

    folder = Path(args.video_folder).resolve()
    if not folder.is_dir():
        raise SystemExit(f"Folder not found: {folder}")

    script_md = folder / "script.md"
    voice_mp3 = folder / "voice" / "voice.mp3"
    images_dir = folder / "images"
    output_path = Path(args.output) if args.output else folder / "final.mp4"

    for path in (script_md, voice_mp3, images_dir):
        if not path.exists():
            raise SystemExit(f"Required file/folder missing: {path}")

    sections = parse_script_sections(script_md)
    if not sections:
        raise SystemExit(
            "script.md has no sections. Separate image segments with a line "
            "containing only '==='."
        )

    image_files = sorted(images_dir.glob("*.png"))
    if len(image_files) != len(sections):
        raise SystemExit(
            f"Mismatch: script.md has {len(sections)} sections (split by ===) "
            f"but images/ contains {len(image_files)} PNG files. "
            f"There must be one image per section."
        )

    audio_duration = get_audio_duration(voice_mp3)
    timings = compute_timings(sections, audio_duration)

    print(f"Auto-calculated image timing (audio = {audio_duration:.1f}s):", file=sys.stderr)
    for i, ((s, e), img) in enumerate(zip(timings, image_files), start=1):
        print(f"  {i}. {img.name}: {s:6.1f}s → {e:6.1f}s  ({e-s:.1f}s)", file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="merge_video_") as tmp:
        tmp_path = Path(tmp)
        clip_paths: list[Path] = []
        clip_durations: list[float] = []

        for i, (image_path, (start, end)) in enumerate(zip(image_files, timings)):
            displayed_duration = end - start
            # Non-last clips get padded so xfade has overlap material to work with.
            clip_duration = (
                displayed_duration + CROSSFADE_DURATION
                if i < len(sections) - 1
                else displayed_duration
            )
            clip_path = tmp_path / f"clip_{i:02d}.mp4"
            print(
                f"Rendering clip {i + 1}/{len(sections)} "
                f"({image_path.name}, {clip_duration:.1f}s)...",
                file=sys.stderr,
            )
            build_image_clip(
                image_path=image_path,
                duration=clip_duration,
                output_path=clip_path,
            )
            clip_paths.append(clip_path)
            clip_durations.append(clip_duration)

        print(f"Merging {len(clip_paths)} clips with audio...", file=sys.stderr)
        merge_clips_with_audio(
            clip_paths=clip_paths,
            clip_durations=clip_durations,
            audio_path=voice_mp3,
            output_path=output_path,
        )

    result = {
        "output_path": str(output_path),
        "ffmpeg_version": ffmpeg_version,
        "audio_duration_seconds": round(audio_duration, 2),
        "section_count": len(sections),
        "image_files": [p.name for p in image_files],
        "crossfade_seconds": CROSSFADE_DURATION,
        "fps": FPS,
        "resolution": "1920x1080",
        "file_size_bytes": output_path.stat().st_size,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
