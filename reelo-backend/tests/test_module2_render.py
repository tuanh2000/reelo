"""Renderer: build-command unit tests (Ken Burns / aspect / xfade / ducking) + smoke."""

from __future__ import annotations

from pathlib import Path

import pytest

from clients.base import CallContext, ImageRequest, ServiceConfig
from clients.stub import PlaceholderImageClient
from keystore import Cipher, KeyStore
from module2 import ffmpeg, render
from usage import UsageLogger

FFMPEG = ffmpeg.ffmpeg_available()
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")


# --------------------------------------------------------------------------- #
# Pure build-command tests                                                    #
# --------------------------------------------------------------------------- #
def test_frame_size_by_aspect():
    assert render.frame_size("16:9") == (1920, 1080)
    assert render.frame_size("9:16") == (1080, 1920)
    assert render.frame_size("weird") == (1920, 1080)  # default


def test_clip_durations_pad_non_final():
    padded = render.clip_durations([4.0, 5.0, 6.0])
    assert padded == [4.0 + render.CROSSFADE, 5.0 + render.CROSSFADE, 6.0]


def test_build_clip_filter_has_kenburns_and_pillarbox():
    f = render.build_clip_filter(1920, 1080, 4.0, index=0)
    # blur-pillarbox background
    assert "gblur=sigma=30" in f
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in f
    assert "crop=1920:1080" in f
    # Ken Burns zoompan present, zoom-in for even index
    assert "zoompan=" in f
    assert f"min(zoom+{render.ZOOM_SPEED},{render.ZOOM_MAX})" in f
    assert "overlay=(W-w)/2:(H-h)/2" in f


def test_build_clip_filter_alternates_zoom_direction():
    even = render.build_clip_filter(1920, 1080, 4.0, index=0)
    odd = render.build_clip_filter(1920, 1080, 4.0, index=1)
    assert f"min(zoom+{render.ZOOM_SPEED}" in even  # zoom in
    assert "max(zoom-" in odd  # zoom out


def test_build_clip_filter_portrait_dims():
    f = render.build_clip_filter(1080, 1920, 3.0, index=0)
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in f
    assert "crop=1080:1920" in f


def test_build_clip_cmd_argv_shape():
    cmd = render.build_clip_cmd(
        Path("images/01.png"), Path("clip_00.mp4"),
        width=1920, height=1080, duration=4.0, index=0,
    )
    assert cmd[0] == ffmpeg.ffmpeg_bin()
    assert "-loop" in cmd and "1" in cmd
    assert "libx264" in cmd
    assert cmd[-1] == "clip_00.mp4"
    # duration appears as -t
    assert "4.000" in cmd


def test_build_xfade_video_filter_chain():
    f, label = render.build_xfade_video_filter([4.6, 4.6, 4.0])
    assert label == "[vout]"
    # two xfade steps for 3 clips
    assert f.count("xfade=transition=fade") == 2
    assert "offset=4.000" in f  # first offset = first padded - 0 ... cumulative
    assert "[vout]" in f


def test_build_xfade_video_filter_single_clip_no_xfade():
    f, label = render.build_xfade_video_filter([4.0])
    assert f == ""
    assert label == "[0:v]"


def test_build_audio_filter_no_music_maps_voice():
    f, label = render.build_audio_filter(voice_idx=3, music_idx=None)
    assert f == ""
    assert label == "3:a"


def test_build_audio_filter_music_has_ducking():
    f, label = render.build_audio_filter(voice_idx=3, music_idx=4)
    assert label == "[aout]"
    assert f"volume={render.MUSIC_VOLUME}" in f
    assert "sidechaincompress=" in f
    assert f"ratio={render.DUCK_RATIO}" in f
    assert "amix=inputs=2" in f


def test_build_xfade_mux_cmd_no_music():
    clips = [Path("c0.mp4"), Path("c1.mp4")]
    cmd = render.build_xfade_mux_cmd(clips, [4.6, 4.0], Path("voice.mp3"), Path("final.mp4"))
    # inputs: 2 clips + voice
    assert cmd.count("-i") == 3
    assert "-stream_loop" not in cmd  # no music
    assert "-map" in cmd
    assert "[vout]" in cmd
    assert "2:a" in cmd  # voice index = 2
    assert "-shortest" in cmd
    assert cmd[-1] == "final.mp4"


def test_build_xfade_mux_cmd_with_music_loops_and_ducks():
    clips = [Path("c0.mp4"), Path("c1.mp4")]
    cmd = render.build_xfade_mux_cmd(
        clips, [4.6, 4.0], Path("voice.mp3"), Path("final.mp4"),
        music_path=Path("bg.mp3"),
    )
    assert "-stream_loop" in cmd
    # music looped input
    si = cmd.index("-stream_loop")
    assert cmd[si + 1] == "-1"
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "sidechaincompress" in fc
    assert "[aout]" in cmd


def test_plan_render_computes_durations():
    plan = render.plan_render(
        [Path("a.png"), Path("b.png")],
        ["one two three four", "five six"],
        audio_duration=60.0,
        aspect="16:9",
    )
    assert plan.width == 1920 and plan.height == 1080
    assert len(plan.displayed) == 2
    # padded: non-final += crossfade
    assert plan.padded[0] == plan.displayed[0] + render.CROSSFADE
    assert plan.padded[1] == plan.displayed[1]


async def test_render_episode_validates_counts(tmp_path):
    with pytest.raises(ValueError):
        await render.render_episode([], [], tmp_path / "v.mp3", tmp_path / "out.mp4", "16:9")


# --------------------------------------------------------------------------- #
# Smoke render (real ffmpeg) — skipped if ffmpeg absent                       #
# --------------------------------------------------------------------------- #
async def _make_silent_mp3(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    await ffmpeg.run([
        ffmpeg.ffmpeg_bin(), "-y", "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=mono", "-t", str(seconds),
        "-c:a", "libmp3lame", "-b:a", "128k", str(path),
    ])


async def _make_tone_mp3(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    await ffmpeg.run([
        ffmpeg.ffmpeg_bin(), "-y", "-f", "lavfi",
        "-i", "sine=frequency=440:r=44100", "-t", str(seconds),
        "-c:a", "libmp3lame", "-b:a", "128k", str(path),
    ])


@needs_ffmpeg
async def test_smoke_render_16x9(tmp_path):
    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    img = PlaceholderImageClient(ServiceConfig("stub-image", {}))
    imgs = []
    for i in range(2):
        p = tmp_path / f"images/{i:02d}_a.png"
        await img.generate_image(ImageRequest(prompt="x", size="16:9"), p, ctx)
        imgs.append(p)
    vp = tmp_path / "voice/voice.mp3"
    await _make_silent_mp3(vp, 2.0)

    out = tmp_path / "final.mp4"
    narr = ["first scene narration text here now", "second scene narration block here too"]
    await render.render_episode(imgs, narr, vp, out, "16:9", work_dir=tmp_path / "clips")

    assert out.exists() and out.stat().st_size > 0
    assert await ffmpeg.probe_dimensions(out) == (1920, 1080)
    assert abs(await ffmpeg.probe_duration(out) - 2.0) < 0.6


@needs_ffmpeg
async def test_smoke_render_9x16_with_music(tmp_path):
    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    img = PlaceholderImageClient(ServiceConfig("stub-image", {}))
    imgs = []
    for i in range(2):
        p = tmp_path / f"images/{i:02d}_a.png"
        await img.generate_image(ImageRequest(prompt="x", size="9:16"), p, ctx)
        imgs.append(p)
    vp = tmp_path / "voice/voice.mp3"
    await _make_silent_mp3(vp, 3.0)
    bg = tmp_path / "music/bg.mp3"
    await _make_tone_mp3(bg, 1.0)  # shorter than video -> must loop

    out = tmp_path / "final.mp4"
    narr = ["portrait one text words here go", "portrait two text words here now"]
    await render.render_episode(
        imgs, narr, vp, out, "9:16", music_path=bg, work_dir=tmp_path / "clips"
    )

    assert out.exists()
    assert await ffmpeg.probe_dimensions(out) == (1080, 1920)
    # audio stream is AAC
    codec = await ffmpeg.run([
        ffmpeg.ffprobe_bin(), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out),
    ])
    assert codec.strip() == "aac"


# --------------------------------------------------------------------------- #
# Video clip path (M2-13) — pure argv + real mixed image+clip render          #
# --------------------------------------------------------------------------- #
def test_build_video_clip_filter_fits_no_kenburns():
    f = render.build_video_clip_filter(1920, 1080)
    # Cover + centre-crop to the exact frame, fps + sar normalized, yuv420p.
    assert "scale=1920:1080:force_original_aspect_ratio=increase" in f
    assert "crop=1920:1080" in f
    assert "fps=30" in f and "setsar=1" in f and "format=yuv420p" in f
    # No Ken Burns on a video clip (the clip already moves).
    assert "zoompan" not in f and "gblur" not in f


def test_build_video_clip_cmd_loops_trims_and_mutes():
    cmd = render.build_video_clip_cmd(
        Path("media/01.mp4"), Path("clip_00.mp4"),
        width=1920, height=1080, duration=4.0, index=1,
    )
    # -stream_loop -1 BEFORE the input so a short clip fills the time, then -t cuts.
    si = cmd.index("-stream_loop")
    ii = cmd.index("-i")
    assert cmd[si + 1] == "-1" and si < ii
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "4.000"
    # Source audio dropped; encode matches the image clips (libx264/crf20/yuv420p).
    assert "-an" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-crf") + 1] == "20"
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[cmd.index("-r") + 1] == "30"


async def _make_test_clip(path: Path, seconds: float, *, size: str = "1280x720") -> None:
    """A real mp4 (testsrc, with a muted audio track) as a 'chosen' video clip."""
    path.parent.mkdir(parents=True, exist_ok=True)
    await ffmpeg.run([
        ffmpeg.ffmpeg_bin(), "-y",
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={seconds}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    ])


@needs_ffmpeg
async def test_smoke_render_mixed_image_and_video(tmp_path):
    """Real proof (M2-13): render 1 Ken-Burns image + 1 looped/muted video clip.

    The source clip (1.0s) is SHORTER than its segment, so the renderer must loop
    it to fill the time. The final.mp4 must be real h264 + aac at the frame size
    and the right total duration — the mixed photo+clip render path end to end.
    """
    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    img_client = PlaceholderImageClient(ServiceConfig("stub-image", {}))

    # Segment 1 = still image (Ken Burns); segment 2 = video clip (fit/loop/mute).
    img = tmp_path / "images/01_a.png"
    await img_client.generate_image(ImageRequest(prompt="x", size="16:9"), img, ctx)
    clip = tmp_path / "media/02_b.mp4"
    await _make_test_clip(clip, 1.0)  # shorter than its segment -> must loop

    vp = tmp_path / "voice/voice.mp3"
    await _make_silent_mp3(vp, 4.0)

    out = tmp_path / "final.mp4"
    narr = ["first scene narration text here now please", "second scene clip narration block here too now"]
    await render.render_episode(
        [img, clip], narr, vp, out, "16:9",
        media_types=["image", "video"], work_dir=tmp_path / "clips",
    )

    assert out.exists() and out.stat().st_size > 0
    assert await ffmpeg.probe_dimensions(out) == (1920, 1080)
    assert abs(await ffmpeg.probe_duration(out) - 4.0) < 0.7
    # Real h264 video + aac audio.
    vcodec = await ffmpeg.run([
        ffmpeg.ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out),
    ])
    acodec = await ffmpeg.run([
        ffmpeg.ffprobe_bin(), "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out),
    ])
    assert vcodec.strip() == "h264"
    assert acodec.strip() == "aac"
