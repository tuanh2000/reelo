"""Timing (word-fraction) + SRT generation."""

from __future__ import annotations

from module2 import subtitles
from module2.timing import compute_timings, count_words, split_sentences


def test_compute_timings_proportional_to_words():
    narrations = ["one two three four", "five six"]  # 4 + 2 words = 6
    timings = compute_timings(narrations, audio_duration=60.0)
    (s0, e0), (s1, e1) = timings
    assert s0 == 0.0
    # first block ~ 4/6 of 60 = 40s
    assert abs(e0 - 40.0) < 0.001
    assert abs(s1 - 40.0) < 0.001
    # last block pinned to the audio duration exactly
    assert e1 == 60.0


def test_compute_timings_empty():
    assert compute_timings([], 10.0) == []


def test_count_words_minimum_one():
    assert count_words("") == 1
    assert count_words("a b c") == 3


def test_split_sentences_latin_and_cjk():
    assert split_sentences("Hello world. How are you?") == [
        "Hello world.",
        "How are you?",
    ]
    # CJK terminator handled
    parts = split_sentences("第一句。第二句！")
    assert len(parts) == 2


def test_split_sentences_no_terminator_returns_whole():
    assert split_sentences("no terminator here") == ["no terminator here"]


def test_build_cues_pins_segment_end():
    narrations = ["First sentence. Second sentence.", "Third only."]
    cues = subtitles.build_cues(narrations, audio_duration=30.0)
    assert [c.index for c in cues] == [1, 2, 3]
    # The first segment (2 sentences) ends where the second segment begins.
    # cue 2 end == cue 3 start (segment boundary at 2/3 -> 3/3 of duration)
    seg1_end = cues[1].end
    assert abs(cues[2].start - seg1_end) < 0.001
    # last cue ends at audio duration
    assert cues[-1].end == 30.0


def test_render_srt_format():
    cues = subtitles.build_cues(["Hello there friend."], audio_duration=2.0)
    text = subtitles.render_srt(cues)
    assert text.startswith("1\n")
    assert "00:00:00,000 --> 00:00:02,000" in text
    assert "Hello there friend." in text


def test_write_srt_roundtrip(tmp_path):
    out = tmp_path / "subs.srt"
    narrations = ["Alpha beta. Gamma delta.", "Epsilon zeta eta."]
    subtitles.write_srt(narrations, 12.0, out)
    content = out.read_text(encoding="utf-8")
    # 3 cues -> three numbered blocks
    assert content.count(" --> ") == 3
    assert "1\n" in content and "3\n" in content
