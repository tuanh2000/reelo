"""Module 1 parse + validate (module-1 §9): clean JSON, dirty JSON, invariants."""

from __future__ import annotations

import pytest

from module1.parse import (
    ParseError,
    ValidationError,
    extract_balanced_braces,
    parse_chunk,
    repair_json,
    slugify,
    validate_chunk,
)

# --- fixtures: clean + dirty representations of the same 2-segment chunk ----
_CLEAN = """{"segments": [
  {"index": 1, "narration": "Mo dau.", "image_prompt": "a sunrise over the plain", "image_label": "sunrise"},
  {"index": 2, "narration": "Tiep theo.", "image_prompt": "a temple at dusk", "image_label": "temple"}
]}"""

_SENTINEL = (
    "Here is the chunk you asked for.\n<<<REELO_SPEC>>>\n" + _CLEAN + "\n<<<END_REELO_SPEC>>>\nDone!"
)

_FENCE = "Sure:\n```json\n" + _CLEAN + "\n```\nthat's it"

_PROSE_BRACES = "Absolutely! " + _CLEAN + " — hope that helps."

# A model that used smart quotes AS JSON delimiters + trailing commas (a common
# defect). repair_json normalises the delimiters and strips the trailing commas.
_DIRTY = """```json
{“segments”: [
  {“index”: 1, “narration”: “Mo dau.”, “image_prompt”: “a sunrise”, “image_label”: “sunrise”,},
  {“index”: 2, “narration”: “Tiep.”, “image_prompt”: “a temple”, “image_label”: “temple”},
]}
```"""


def _check_two(data):
    segs = validate_chunk(data, expected_count=2, idx_start=1)
    assert [s.index for s in segs] == [1, 2]
    return segs


def test_parse_clean_json():
    _check_two(parse_chunk(_CLEAN))


def test_parse_sentinel_block():
    _check_two(parse_chunk(_SENTINEL))


def test_parse_json_fence():
    _check_two(parse_chunk(_FENCE))


def test_parse_balanced_braces_from_prose():
    _check_two(parse_chunk(_PROSE_BRACES))


def test_parse_dirty_json_repaired():
    # smart-quote delimiters + trailing commas → repaired then parsed
    data = parse_chunk(_DIRTY)
    segs = _check_two(data)
    assert segs[0].narration == "Mo dau."  # value content intact after repair


def test_repair_json_strips_trailing_comma_and_smart_quotes():
    import json

    fixed = repair_json('{“b”: [1, 2,],}')
    assert json.loads(fixed) == {"b": [1, 2]}


def test_parse_priority_sentinel_wins_over_fence():
    # sentinel block holds the real 2 segments; a decoy fence holds garbage
    raw = (
        "```json\n{\"segments\": []}\n```\n"
        "<<<REELO_SPEC>>>\n" + _CLEAN + "\n<<<END_REELO_SPEC>>>"
    )
    _check_two(parse_chunk(raw))


def test_parse_no_block_raises():
    with pytest.raises(ParseError):
        parse_chunk("there is no json here at all")


def test_parse_empty_raises():
    with pytest.raises(ParseError):
        parse_chunk("   ")


def test_extract_balanced_braces_ignores_braces_in_strings():
    raw = 'noise {"k": "a } b { c"} tail'
    block = extract_balanced_braces(raw)
    assert block == '{"k": "a } b { c"}'


def test_extract_balanced_braces_truncated_returns_none():
    assert extract_balanced_braces('{"segments": [{"index": 1') is None


# --- validation invariants -------------------------------------------------
def test_validate_wrong_count():
    with pytest.raises(ValidationError):
        validate_chunk(parse_chunk(_CLEAN), expected_count=3, idx_start=1)


def test_validate_non_contiguous_index():
    data = {"segments": [
        {"index": 1, "narration": "a", "image_prompt": "x", "image_label": "l1"},
        {"index": 3, "narration": "b", "image_prompt": "y", "image_label": "l2"},
    ]}
    with pytest.raises(ValidationError):
        validate_chunk(data, expected_count=2, idx_start=1)


def test_validate_idx_start_offset():
    data = {"segments": [
        {"index": 9, "narration": "a", "image_prompt": "x", "image_label": "l1"},
        {"index": 10, "narration": "b", "image_prompt": "y", "image_label": "l2"},
    ]}
    segs = validate_chunk(data, expected_count=2, idx_start=9)
    assert [s.index for s in segs] == [9, 10]


def test_validate_empty_narration_rejected():
    data = {"segments": [{"index": 1, "narration": "   ", "image_prompt": "x", "image_label": "l"}]}
    with pytest.raises(ValidationError):
        validate_chunk(data, expected_count=1, idx_start=1)


def test_validate_empty_image_prompt_rejected():
    data = {"segments": [{"index": 1, "narration": "a", "image_prompt": "", "image_label": "l"}]}
    with pytest.raises(ValidationError):
        validate_chunk(data, expected_count=1, idx_start=1)


def test_validate_missing_label_is_derived():
    data = {"segments": [{"index": 1, "narration": "a", "image_prompt": "a quiet temple at dawn"}]}
    segs = validate_chunk(data, expected_count=1, idx_start=1)
    assert segs[0].image_label == "a-quiet-temple-at-dawn"


def test_validate_missing_segments_key():
    with pytest.raises(ValidationError):
        validate_chunk({"foo": []}, expected_count=0, idx_start=1)


def test_slugify():
    assert slugify("The Origin of  the Gods!!") == "the-origin-of-the-gods"
    assert slugify("") == "scene"
