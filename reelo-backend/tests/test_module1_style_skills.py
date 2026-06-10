"""Module 1 skill templates + presets + image-style resolution + inferStyle."""

from __future__ import annotations

import pytest

from models.spec import ImageStyle
from module1.skills import SkillTemplateError, load_skill_template
from module1.style import get_preset, infer_style, list_presets, resolve_image_style


# --- skill templates -------------------------------------------------------
def test_religion_template_is_complete():
    t = load_skill_template("religion")
    assert t.script.structure[0] == "hook"
    assert "layer_literal" in t.script.structure
    assert t.script.word_ratios["layers"] == pytest.approx(0.60)
    assert t.script.rule_prompt_extra  # non-empty guidance
    assert t.image.recommended_preset == "painterly-devotional"
    assert t.style_layer_for("islam")  # tradition layer present
    assert "Muhammad" in t.style_layer_for("islam")  # iconographic rule carried
    assert t.voice.default_voice_id == "JBFqnCBsd6RMkjVDRZzb"
    assert t.voice.settings["speed"] == 0.92


@pytest.mark.parametrize("skill", ["story", "explain", "news"])
def test_general_skill_templates_are_real_and_topic_agnostic(skill):
    """explain/story/news are real, general-purpose writing styles (no TODO
    placeholders, no genre restriction, no per-tradition image layers)."""
    t = load_skill_template(skill)
    assert t.script.structure
    assert t.script.word_ratios
    # word_ratios sum to ~1.0 (a sane distribution).
    assert abs(sum(t.script.word_ratios.values()) - 1.0) < 0.01
    assert t.image.recommended_preset
    # Real guidance, not a scaffold placeholder.
    extra = t.script.rule_prompt_extra
    assert extra and "TODO" not in extra
    # Topic-agnostic: usable for ANY subject (e.g. endangered animals).
    assert "ANY" in extra or "any" in extra
    assert "NO subject restriction" in extra
    # General skills do not gate on a religious tradition.
    assert t.image.style_layers == {}
    assert t.style_layer_for("islam") is None


def test_explain_structure_is_explainer_shaped():
    t = load_skill_template("explain")
    assert t.script.structure[0] == "hook"
    assert "key_points" in t.script.structure
    assert t.script.structure[-1] == "closing"


def test_religion_skill_unchanged_and_still_scholarly():
    """Religion stays specialised: scholarly rule + per-tradition image layers."""
    t = load_skill_template("religion")
    assert "three-layer method" in t.script.rule_prompt_extra
    assert t.style_layer_for("islam") and "Muhammad" in t.style_layer_for("islam")


def test_unknown_skill_raises():
    with pytest.raises(SkillTemplateError):
        load_skill_template("does-not-exist")


def test_style_layer_for_none():
    assert load_skill_template("religion").style_layer_for(None) is None


# --- presets ---------------------------------------------------------------
def test_six_ui_presets_plus_devotional_present():
    ids = {p.preset_id for p in list_presets()}
    assert {
        "cinematic", "documentary", "animated", "minimal", "vintage", "noir",
        "painterly-devotional",
    } <= ids


def test_every_preset_has_english_base_prompt_and_palette():
    for p in list_presets():
        assert len(p.base_prompt) > 30
        assert p.palette
        assert all(c.startswith("#") for c in p.palette)


def test_painterly_devotional_is_verbatim_base_style():
    p = get_preset("painterly-devotional")
    # The exact opening of the SKILL.md BASE STYLE block.
    assert p.base_prompt.startswith(
        "Soft painterly realism in the tradition of contemporary devotional illustration"
    )
    assert "Greg Olsen or Liz Lemon Swindle" in p.base_prompt


# --- resolve_image_style (D4) ---------------------------------------------
def test_resolve_combines_preset_and_skill_layer():
    style = resolve_image_style(
        preset_id="painterly-devotional", skill="religion", tradition="islam", aspect="9:16"
    )
    assert isinstance(style, ImageStyle)
    assert style.preset_id == "painterly-devotional"
    assert style.base_prompt.startswith("Soft painterly realism")
    assert style.style_layer and "Kaaba" in style.style_layer
    assert style.aspect == "9:16"
    assert style.palette  # from preset


def test_resolve_palette_and_description_override():
    style = resolve_image_style(
        preset_id="cinematic", skill="story", palette=["#abcdef"], description="custom look"
    )
    assert style.palette == ["#abcdef"]
    assert style.description == "custom look"
    assert style.style_layer is None  # story has no layers


# --- inferStyle ------------------------------------------------------------
def _solid_png(rgb=(180, 100, 40)) -> bytes:
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    w = h = 4
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = (b"\x00" + bytes(rgb) * w) * h
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def test_infer_style_from_png_extracts_palette():
    out = infer_style([_solid_png((180, 100, 40))])
    assert out["palette"]
    assert out["palette"][0].lower() == "#b46428"  # 180,100,40
    assert out["description"]


def test_infer_style_default_when_no_decodable_images():
    out = infer_style([b"not an image"])
    assert out["palette"]  # default palette
    assert out["description"]


def test_infer_style_empty():
    out = infer_style([])
    assert out["palette"] and out["description"]
