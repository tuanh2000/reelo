"""Reelo-native renderer (`render.py`) — replaces the skill's ``merge_video.py``.

Two-phase structure inherited from the skill (render each image to a clip, then
xfade-chain them), extended with **Ken Burns** (slow ``zoompan``), per-series
**aspect** (16:9→1920×1080, 9:16→1080×1920), and optional **music ducking**
(sidechaincompress) under the voice. Image timing is the inherited word-count
algorithm (:mod:`module2.timing`).

Build-command vs exec are split: every ``build_*_cmd`` is a pure function that
returns the exact ffmpeg argv (unit-tested by string match); :func:`render_episode`
is the only thing that touches the filesystem / subprocess.

----------------------------------------------------------------------------
Canonical ffmpeg commands (see ``build_clip_cmd`` / ``build_xfade_mux_cmd``):

Per-clip (Ken Burns + blur-pillarbox), W×H = target frame, d = clip duration::

    ffmpeg -y -loop 1 -t {d} -i images/NN.png -filter_complex "
      [0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},
           gblur=sigma=30,eq=brightness=-0.25,setsar=1,fps=30[bg];
      [0:v]scale={2W}:-2,
           zoompan=z='{zexpr}':d={frames}:x='{xexpr}':y='{yexpr}':
                   s={fgW}x{fgH}:fps=30,setsar=1[fg];
      [bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]
    " -map "[v]" -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -r 30 -t {d} clip_NN.mp4

  Zoom direction alternates by index (in vs out) so the motion is not monotonous.

Xfade chain + audio mux (voice, optionally + ducked/looped music)::

    ffmpeg -y -i clip_00.mp4 ... -i clip_{N-1}.mp4 \\
      -i voice/voice.mp3 [-stream_loop -1 -i music/bg.mp3] \\
      -filter_complex "
        [0:v][1:v]xfade=transition=fade:duration=0.6:offset={o1}[vx1]; ... [vout]; # video
        # with music (idx M = music, V = voice):
        [M:a]volume=0.25,aformat=...[mraw];
        [mraw][V:a]sidechaincompress=threshold=0.03:ratio=8:attack=5:release=300[mduck];
        [V:a][mduck]amix=inputs=2:duration=first:dropout_transition=0,
                     dynaudnorm[a]
      " -map "[vout]" -map "[a]" -c:v libx264 -preset medium -crf 20 \\
        -pix_fmt yuv420p -c:a aac -b:a 192k -shortest final.mp4

  Without music: ``-map [vout] -map {V}:a`` directly (like the skill).
----------------------------------------------------------------------------
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from models.spec import Aspect

from module2 import ffmpeg
from module2.timing import compute_timings

log = logging.getLogger("reelo.module2.render")

FPS = 30
CROSSFADE = 0.6  # seconds
ZOOM_SPEED = 0.0008  # per-frame zoom increment
ZOOM_MAX = 1.20
BLUR_SIGMA = 30
BG_BRIGHTNESS = -0.25
MUSIC_VOLUME = 0.25
DUCK_THRESHOLD = 0.03
DUCK_RATIO = 8
DUCK_ATTACK = 5
DUCK_RELEASE = 300

# aspect -> (width, height) target frame (D8)
FRAME_BY_ASPECT: dict[str, tuple[int, int]] = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
}


def frame_size(aspect: Aspect | str) -> tuple[int, int]:
    """Target render frame for a series aspect (defaults to 16:9)."""
    return FRAME_BY_ASPECT.get(str(aspect), FRAME_BY_ASPECT["16:9"])


def clip_durations(displayed: list[float]) -> list[float]:
    """Pad non-final clip durations by the crossfade so xfade has overlap material.

    Mirrors the skill: a clip that crossfades into the next must be CROSSFADE
    seconds longer than its displayed time. The last clip is not padded.
    """
    n = len(displayed)
    return [
        d + CROSSFADE if i < n - 1 else d for i, d in enumerate(displayed)
    ]


def _zoompan_exprs(index: int) -> tuple[str, str, str]:
    """Return ``(z, x, y)`` zoompan expressions; direction alternates by index.

    Even index → zoom-in (1.0 → ZOOM_MAX); odd index → zoom-out (ZOOM_MAX → 1.0).
    Both pan toward centre so the framing stays on the subject.
    """
    cx = "iw/2-(iw/zoom/2)"
    cy = "ih/2-(ih/zoom/2)"
    if index % 2 == 0:
        z = f"min(zoom+{ZOOM_SPEED},{ZOOM_MAX})"
    else:
        # start zoomed-in and ease out toward 1.0
        z = f"if(eq(on,0),{ZOOM_MAX},max(zoom-{ZOOM_SPEED},1.0))"
    return z, cx, cy


def build_clip_filter(width: int, height: int, duration: float, index: int) -> str:
    """Build the per-clip ``-filter_complex`` string (Ken Burns + blur pillarbox).

    Pure: returned verbatim so tests can assert the exact filtergraph.
    """
    frames = max(1, round(duration * FPS))
    z, x, y = _zoompan_exprs(index)
    # Foreground render size = 2× frame on the long edge so zoompan has pixels to
    # crop into without softening; zoompan outputs exactly the frame size.
    fg_w, fg_h = width, height
    return (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma={BLUR_SIGMA},"
        f"eq=brightness={BG_BRIGHTNESS},setsar=1,fps={FPS}[bg];"
        f"[0:v]scale={width * 2}:-2,"
        f"zoompan=z='{z}':d={frames}:x='{x}':y='{y}':"
        f"s={fg_w}x{fg_h}:fps={FPS},setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )


def build_clip_cmd(
    image_path: Path,
    out_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
    index: int,
) -> list[str]:
    """ffmpeg argv to render one image → a Ken Burns clip of ``duration`` seconds."""
    return [
        ffmpeg.ffmpeg_bin(),
        "-y",
        "-loop",
        "1",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(image_path),
        "-filter_complex",
        build_clip_filter(width, height, duration, index),
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-t",
        f"{duration:.3f}",
        str(out_path),
    ]


def build_video_clip_filter(width: int, height: int) -> str:
    """Per-clip filtergraph for a real **video** clip (M2-13): fit to frame, fps.

    No Ken Burns (the clip already moves). Scale to *cover* the frame
    (force_original_aspect_ratio=increase), centre-crop to exactly W×H, normalize
    fps and SAR. Audio is dropped by the command's ``-an`` (narration + music come
    from the mux step). Pure: returned verbatim so tests can assert it.
    """
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={FPS},setsar=1,format=yuv420p"
    )


def build_video_clip_cmd(
    video_path: Path,
    out_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
    index: int,
) -> list[str]:
    """ffmpeg argv: a real video clip → a frame-fitted, ``duration``-second clip.

    Decisions (M2-13, already chosen): take from the START of the source; if the
    source is SHORTER than ``duration`` it is looped (``-stream_loop -1``) so the
    clip fills the time; the source audio is dropped (``-an``) so only narration +
    background music play; output matches the image clips exactly (libx264, crf20,
    yuv420p, fps=30, setsar=1) so the xfade chain + mux treat them identically.

    ``-stream_loop -1`` before the input loops the file indefinitely; the trailing
    ``-t {duration}`` then cuts it to length (a no-op when the source is longer).
    ``index`` is accepted for signature parity with :func:`build_clip_cmd` (the
    fit is direction-agnostic so it is unused here).
    """
    return [
        ffmpeg.ffmpeg_bin(),
        "-y",
        "-stream_loop",
        "-1",
        "-i",
        str(video_path),
        "-t",
        f"{duration:.3f}",
        "-an",
        "-vf",
        build_video_clip_filter(width, height),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        str(out_path),
    ]


def build_xfade_video_filter(durations: list[float]) -> tuple[str, str]:
    """Build the xfade chain filtergraph + the final video label.

    ``durations`` are the padded clip durations. Returns ``(filter_str, out_label)``.
    With a single clip, returns ``("", "[0:v]")`` (no xfade needed).
    """
    n = len(durations)
    if n == 1:
        return "", "[0:v]"
    parts: list[str] = []
    last_label = "[0:v]"
    cumulative = durations[0]
    for i in range(1, n):
        offset = cumulative - CROSSFADE
        out_label = "[vout]" if i == n - 1 else f"[vx{i}]"
        parts.append(
            f"{last_label}[{i}:v]xfade=transition=fade:"
            f"duration={CROSSFADE}:offset={offset:.3f}{out_label}"
        )
        cumulative += durations[i] - CROSSFADE
        last_label = out_label
    return ";".join(parts), "[vout]"


def build_audio_filter(
    voice_idx: int, music_idx: int | None
) -> tuple[str, str]:
    """Build the audio filtergraph + final audio label.

    Without music: ``("", "{voice_idx}:a")`` — map the voice stream directly.
    With music: loop (handled by ``-stream_loop`` on the input), lower its volume,
    sidechain-duck it under the voice, then mix; ``dynaudnorm`` evens the result.
    """
    if music_idx is None:
        return "", f"{voice_idx}:a"
    parts = (
        f"[{music_idx}:a]volume={MUSIC_VOLUME},"
        f"aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[mraw];"
        f"[mraw][{voice_idx}:a]sidechaincompress="
        f"threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}:"
        f"attack={DUCK_ATTACK}:release={DUCK_RELEASE}[mduck];"
        f"[{voice_idx}:a][mduck]amix=inputs=2:duration=first:dropout_transition=0,"
        f"dynaudnorm[aout]"
    )
    return parts, "[aout]"


def build_xfade_mux_cmd(
    clip_paths: list[Path],
    durations: list[float],
    voice_path: Path,
    out_path: Path,
    *,
    music_path: Path | None = None,
) -> list[str]:
    """ffmpeg argv for the xfade chain + audio mux (voice, optional ducked music).

    Inputs are ``clip_0..clip_{N-1}``, then voice, then (if any) music with
    ``-stream_loop -1`` so it covers the whole video. ``-shortest`` trims the
    looped music to the video length.
    """
    n = len(clip_paths)
    argv: list[str] = [ffmpeg.ffmpeg_bin(), "-y"]
    for clip in clip_paths:
        argv += ["-i", str(clip)]
    argv += ["-i", str(voice_path)]
    voice_idx = n
    music_idx: int | None = None
    if music_path is not None:
        argv += ["-stream_loop", "-1", "-i", str(music_path)]
        music_idx = n + 1

    vfilter, vlabel = build_xfade_video_filter(durations)
    afilter, alabel = build_audio_filter(voice_idx, music_idx)
    graph = ";".join(p for p in (vfilter, afilter) if p)

    if graph:
        argv += ["-filter_complex", graph]
    # Map video: filtered label (needs -map "[vout]") or raw stream "0:v".
    argv += ["-map", vlabel]
    argv += ["-map", alabel]
    argv += [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_path),
    ]
    return argv


@dataclass
class RenderPlan:
    """Everything the render needs, computed up front (pure, inspectable)."""

    width: int
    height: int
    image_paths: list[Path]
    displayed: list[float] = field(default_factory=list)
    padded: list[float] = field(default_factory=list)
    # Per-clip media kind ("image" | "video"), parallel to ``image_paths``.
    media_types: list[str] = field(default_factory=list)


def plan_render(
    image_paths: list[Path],
    narrations: list[str],
    audio_duration: float,
    aspect: Aspect | str,
    *,
    media_types: list[str] | None = None,
) -> RenderPlan:
    """Compute per-clip durations + frame size + media kinds (no I/O)."""
    w, h = frame_size(aspect)
    timings = compute_timings(narrations, audio_duration)
    displayed = [e - s for s, e in timings]
    padded = clip_durations(displayed)
    kinds = list(media_types) if media_types else ["image"] * len(image_paths)
    return RenderPlan(
        width=w,
        height=h,
        image_paths=list(image_paths),
        displayed=displayed,
        padded=padded,
        media_types=kinds,
    )


async def render_episode(
    image_paths: list[Path],
    narrations: list[str],
    voice_path: Path,
    out_path: Path,
    aspect: Aspect | str,
    *,
    media_types: list[str] | None = None,
    music_path: Path | None = None,
    work_dir: Path | None = None,
    clip_timeout: float | None = 600,
    mux_timeout: float | None = 1800,
) -> Path:
    """Render an episode → ``out_path`` (final.mp4). The only I/O entrypoint here.

    Args:
        image_paths: media files in segment order (1 per narration block). A path
            is an image PNG/JPG OR a video mp4 depending on ``media_types``.
        narrations: narration text per segment (for word-count timing).
        voice_path: ``voice/voice.mp3``.
        out_path: where to write ``final.mp4``.
        aspect: series aspect (``16:9`` | ``9:16``).
        media_types: per-segment ``"image"`` | ``"video"`` (defaults to all
            images — backward-compatible). Images get Ken Burns; video clips are
            fit/looped/muted to the segment duration (M2-13). Both produce a
            uniform ``clip_NN.mp4`` so the xfade chain + audio mux are unchanged.
        music_path: optional ``music/bg.mp3`` to duck under the voice.
        work_dir: where per-clip mp4s are written (defaults to ``out_path``'s dir).

    Returns:
        ``out_path``.

    Raises:
        ValueError: if the image / narration counts mismatch or are empty.
        FFmpegError: on any ffmpeg failure (carries stderr).
    """
    if not image_paths:
        raise ValueError("no images to render")
    if len(image_paths) != len(narrations):
        raise ValueError(
            f"image/narration count mismatch: {len(image_paths)} vs {len(narrations)}"
        )
    if media_types is not None and len(media_types) != len(image_paths):
        raise ValueError(
            f"media_types/image count mismatch: {len(media_types)} vs {len(image_paths)}"
        )
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    work = Path(work_dir) if work_dir else out_path.parent / "clips"
    work.mkdir(parents=True, exist_ok=True)

    duration = await ffmpeg.probe_duration(voice_path)
    plan = plan_render(image_paths, narrations, duration, aspect, media_types=media_types)

    clip_paths: list[Path] = []
    for i, (media, d, kind) in enumerate(
        zip(plan.image_paths, plan.padded, plan.media_types)
    ):
        clip = work / f"clip_{i:02d}.mp4"
        if kind == "video":
            cmd = build_video_clip_cmd(
                media, clip, width=plan.width, height=plan.height, duration=d, index=i
            )
        else:
            cmd = build_clip_cmd(
                media, clip, width=plan.width, height=plan.height, duration=d, index=i
            )
        log.info(
            "render clip %d/%d (%.1fs, %s) %s",
            i + 1, len(image_paths), d, kind, media.name,
        )
        await ffmpeg.run(cmd, timeout=clip_timeout)
        clip_paths.append(clip)

    use_music = music_path is not None and Path(music_path).exists()
    cmd = build_xfade_mux_cmd(
        clip_paths,
        plan.padded,
        Path(voice_path),
        out_path,
        music_path=Path(music_path) if use_music else None,
    )
    log.info("xfade+mux %d clips -> %s (music=%s)", len(clip_paths), out_path, use_music)
    await ffmpeg.run(cmd, timeout=mux_timeout)
    return out_path


__all__ = [
    "FPS",
    "CROSSFADE",
    "FRAME_BY_ASPECT",
    "frame_size",
    "clip_durations",
    "build_clip_filter",
    "build_clip_cmd",
    "build_video_clip_filter",
    "build_video_clip_cmd",
    "build_xfade_video_filter",
    "build_audio_filter",
    "build_xfade_mux_cmd",
    "RenderPlan",
    "plan_render",
    "render_episode",
]
