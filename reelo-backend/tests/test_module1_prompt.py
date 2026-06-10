"""Module 1 density math, chunk planning, schema + prompt builders (§5/§8/§9)."""

from __future__ import annotations

from models.spec import EpisodeSpec, ImageStyle, SeriesSpec, VoiceConfig

from module1.prompt import (
    MAX_SEGMENTS,
    MIN_SEGMENTS,
    SEGMENTS_PER_CHUNK,
    build_chunk_system,
    build_chunk_user,
    build_phase_a_system,
    derive_segment_count,
    derive_word_budget,
    plan_chunks,
    segments_json_schema,
    wpm_for,
)
from module1.skills import load_skill_template


# --- density tables (§5) ---------------------------------------------------
def test_segment_count_matches_spec_table():
    # module-1 §5 example rows
    assert derive_segment_count(10, "light") == 10
    assert derive_segment_count(10, "standard") == 17
    assert derive_segment_count(10, "dense") == 27
    assert derive_segment_count(25, "light") == 25
    assert derive_segment_count(25, "standard") == 43
    assert derive_segment_count(25, "dense") == 68


def test_segment_count_clamped():
    assert derive_segment_count(0.1, "light") == MIN_SEGMENTS  # tiny → floor 3
    assert derive_segment_count(100000, "dense") == MAX_SEGMENTS  # huge → ceiling


def test_word_budget():
    assert wpm_for("en") == 150
    assert wpm_for("vi") == 140
    b = derive_word_budget(10, "en", 17)
    assert b.total_words == 1500
    assert b.words_per_segment == round(1500 / 17)


# --- chunk planning (§8) ---------------------------------------------------
def test_plan_chunks_sum_and_contiguous():
    tmpl = load_skill_template("religion")
    n = 17
    chunks = plan_chunks(n, tmpl.script.structure, tmpl.script.word_ratios)
    assert sum(c.count for c in chunks) == n
    # idx tiles 1..n with no gaps/overlap
    idx = 1
    for c in chunks:
        assert c.idx_start == idx
        assert c.count <= SEGMENTS_PER_CHUNK
        idx += c.count
    assert idx - 1 == n


def test_plan_chunks_caps_per_chunk():
    # 100 segments, single section → many capped chunks
    chunks = plan_chunks(100, ["body"], {"body": 1.0})
    assert all(c.count <= SEGMENTS_PER_CHUNK for c in chunks)
    assert sum(c.count for c in chunks) == 100
    assert len(chunks) == (100 + SEGMENTS_PER_CHUNK - 1) // SEGMENTS_PER_CHUNK


def test_plan_chunks_layers_group_split_evenly():
    # 'layers' umbrella ratio should be distributed across the 3 layer_* sections
    tmpl = load_skill_template("religion")
    chunks = plan_chunks(30, tmpl.script.structure, tmpl.script.word_ratios)
    layer_total = sum(c.count for c in chunks if c.section.startswith("layer"))
    # layers ratio is 0.60 of 30 = ~18
    assert 16 <= layer_total <= 20


def test_plan_chunks_zero():
    assert plan_chunks(0, ["hook"], {}) == []


def test_plan_chunks_even_when_no_ratios():
    chunks = plan_chunks(6, ["a", "b", "c"], {})
    counts = {c.section: c.count for c in chunks}
    assert counts == {"a": 2, "b": 2, "c": 2}


# --- json schema (§9) ------------------------------------------------------
def test_segments_schema_shape():
    schema = segments_json_schema("vi")
    seg = schema["properties"]["segments"]["items"]
    # image_query is an OPTIONAL property (M2-11 real-photo providers): present
    # in properties but NOT required, so it stays backward-compatible.
    assert set(seg["properties"]) == {
        "index", "narration", "image_prompt", "image_label", "image_query",
    }
    assert set(seg["required"]) == {"index", "narration", "image_prompt", "image_label"}


# --- prompt builders -------------------------------------------------------
def _series() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="Faiths", topic="religion", skill="religion",
        language="vi", target_minutes=10, density="standard",
        providers={"script": "stub-script", "image": "kie", "voice": "edge"},
        image_style=ImageStyle(preset_id="painterly-devotional", base_prompt="b"),
        voice=VoiceConfig(provider="edge", voice_id="v"),
        episodes=[],
    )


def test_phase_a_system_is_topic_agnostic():
    """Phase A is a GENERAL video-planning assistant: language + outline format +
    clarifying-question behaviour, and explicitly NO genre restriction and NO
    skill rule_prompt_extra leaking into the chat (§4)."""
    sys = build_phase_a_system("Tieng Viet")
    assert "Tieng Viet" in sys
    assert "<<<OUTLINE>>>" in sys
    assert "ASK a brief clarifying question" in sys
    assert "ANY topic" in sys
    assert "Never refuse" in sys
    # The religion skill's content gate must NOT be present in the chat prompt.
    rel = load_skill_template("religion")
    assert rel.script.rule_prompt_extra not in sys
    assert "three-layer method" not in sys
    assert "ALREADY a believer" not in sys


def test_chunk_system_states_language_and_english_images():
    sys = build_chunk_system(_series(), load_skill_template("religion"))
    assert "vi" in sys
    assert "ALWAYS in English" in sys
    assert "JSON object" in sys


def test_chunk_user_states_index_range_and_count():
    from module1.prompt import ChunkPlan

    ep = EpisodeSpec(episode_id="e1", title="Origins", order=1, desc="start")
    user = build_chunk_user(
        _series(), ep, ChunkPlan(section="hook", count=3, idx_start=5), words_per_segment=80
    )
    assert "5..7" in user
    assert "Origins" in user
