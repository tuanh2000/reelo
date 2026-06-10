"""Materializer: project-folder layout, prompt composition, count invariant."""

from __future__ import annotations

import pytest

from models.spec import (
    EpisodeSpec,
    ImageStyle,
    SegmentSpec,
    SeriesSpec,
    VoiceConfig,
)
from module2 import materialize as mat


def _series(aspect: str = "16:9") -> SeriesSpec:
    return SeriesSpec(
        series_id="s1",
        name="Faiths",
        topic="religion",
        skill="religion",
        language="vi",
        target_minutes=5,
        density="standard",
        providers={"script": "stub-script", "image": "stub-image", "voice": "stub-voice"},
        image_style=ImageStyle(
            preset_id="painterly-devotional",
            base_prompt="oil painting, devotional",
            style_layer="western christian iconography",
            palette=["#112244"],
            aspect=aspect,
        ),
        voice=VoiceConfig(provider="stub-voice", voice_id="v"),
    )


def _episode(n: int = 3) -> EpisodeSpec:
    segs = [
        SegmentSpec(
            index=i,
            narration=f"Narration block number {i} with several words here.",
            image_prompt=f"a temple scene {i}",
            image_label=f"Temple Scene {i}",
        )
        for i in range(1, n + 1)
    ]
    return EpisodeSpec(episode_id="e1", title="My Episode!", order=1, status="scripted", segments=segs)


def test_compose_image_prompt_joins_base_style_segment():
    series = _series()
    prompt = mat.compose_image_prompt(series, "a temple at dawn")
    assert "oil painting, devotional" in prompt
    assert "western christian iconography" in prompt
    assert "a temple at dawn" in prompt
    # order: base, style_layer, segment
    assert prompt.index("oil painting") < prompt.index("western christian")
    assert prompt.index("western christian") < prompt.index("a temple at dawn")


def test_compose_image_prompt_skips_empty_layers():
    series = _series()
    series.image_style.style_layer = None
    prompt = mat.compose_image_prompt(series, "scene")
    assert "\n\n\n" not in prompt  # no empty blocks


def test_image_filename_zero_padded_and_slugged():
    assert mat.image_filename(1, "Temple Scene") == "01_temple_scene"
    assert mat.image_filename(12, "A/B C") == "12_a_b_c"


def test_materialize_writes_script_and_prompts(tmp_path):
    series, ep = _series(), _episode(3)
    lo = mat.materialize(series, ep, tmp_path)

    assert lo.script_md.exists()
    text = lo.script_md.read_text(encoding="utf-8")
    # count(===) + 1 == len(segments)
    assert text.count("===") == len(ep.segments) - 1
    assert mat.count_sections(lo.script_md) == len(ep.segments)

    # one prompt file per segment, zero-padded, segment order
    txts = sorted(lo.images_dir.glob("*.txt"))
    assert len(txts) == len(ep.segments)
    assert txts[0].name == "01_temple_scene_1.txt"
    # prompt content composed
    assert "oil painting" in txts[0].read_text(encoding="utf-8")


def test_materialize_invariant_holds_after_images(tmp_path):
    series, ep = _series(), _episode(3)
    lo = mat.materialize(series, ep, tmp_path)
    # No PNGs yet -> invariant fails.
    with pytest.raises(mat.MaterializeInvariantError):
        mat.verify_invariant(ep, lo)
    # Create exactly one PNG per segment.
    for p in mat.expected_image_paths(ep, lo):
        p.write_bytes(b"\x89PNG")
    mat.verify_invariant(ep, lo)  # now passes


def test_materialize_invariant_detects_missing_png(tmp_path):
    series, ep = _series(), _episode(3)
    lo = mat.materialize(series, ep, tmp_path)
    paths = mat.expected_image_paths(ep, lo)
    for p in paths[:-1]:  # miss the last one
        p.write_bytes(b"\x89PNG")
    with pytest.raises(mat.MaterializeInvariantError):
        mat.verify_invariant(ep, lo)


def test_materialize_copies_music_when_present(tmp_path):
    series, ep = _series(), _episode(2)
    music = tmp_path / "src.mp3"
    music.write_bytes(b"ID3fake")
    lo = mat.materialize(series, ep, tmp_path / "proj", music_src=music)
    assert lo.music_bg.exists()
    assert lo.music_bg.read_bytes() == b"ID3fake"


def test_materialize_rejects_unscripted_episode(tmp_path):
    series = _series()
    ep = EpisodeSpec(episode_id="e9", title="empty", order=1)  # no segments
    with pytest.raises(ValueError):
        mat.materialize(series, ep, tmp_path)


def test_layout_paths_for_aspect_independent(tmp_path):
    lo = mat.layout_for(tmp_path)
    assert lo.voice_mp3 == tmp_path / "voice" / "voice.mp3"
    assert lo.subs_srt == tmp_path / "subs.srt"
    assert lo.final_mp4 == tmp_path / "final.mp4"
    assert lo.image_png(2, "x").name == "02_x.png"
