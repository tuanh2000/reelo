"""Voice orchestration: section read, char-limit chunking, concat command, synth flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from clients.base import (
    AIClient,
    CallContext,
    ServiceConfig,
    Task,
    VoiceRequest,
    VoiceResult,
)
from keystore import Cipher, KeyStore
from models.spec import ImageStyle, SeriesSpec, VoiceConfig
from module2 import ffmpeg, voice
from module2.materialize import layout_for
from usage import UsageLogger


# --------------------------------------------------------------------------- #
# read_sections / split_by_char_limit (pure)                                  #
# --------------------------------------------------------------------------- #
def test_read_sections_splits_on_marker(tmp_path):
    script = tmp_path / "script.md"
    script.write_text("Block one.\n\n===\n\nBlock two.\n\n===\n\nBlock three.")
    assert voice.read_sections(script) == ["Block one.", "Block two.", "Block three."]


def test_split_by_char_limit_packs_greedily():
    sections = ["a" * 100, "b" * 100, "c" * 100]
    chunks = voice.split_by_char_limit(sections, limit=250)
    # 100 + 2 + 100 = 202 <= 250 -> first two together; third alone
    assert len(chunks) == 2
    assert chunks[0].count("a") == 100 and chunks[0].count("b") == 100
    assert chunks[1].count("c") == 100


def test_split_by_char_limit_oversize_section_own_chunk():
    sections = ["x" * 500, "y" * 10]
    chunks = voice.split_by_char_limit(sections, limit=100)
    assert chunks[0] == "x" * 500  # never split a section
    assert chunks[1] == "y" * 10


def test_split_by_char_limit_all_fit_single_chunk():
    chunks = voice.split_by_char_limit(["aa", "bb", "cc"], limit=1000)
    assert len(chunks) == 1
    assert chunks[0] == "aa\n\nbb\n\ncc"


def test_resolve_char_limit_reads_config_and_defaults():
    cfg = ServiceConfig("edge", {"tasks": {"generate-voice": {"char_limit": 8000}}})

    class _C(AIClient):
        capabilities = {Task.GENERATE_VOICE}

    c = _C(cfg)
    assert voice.resolve_char_limit(c) == 8000

    cfg2 = ServiceConfig("x", {"tasks": {"generate-voice": {}}})
    assert voice.resolve_char_limit(_C(cfg2)) == voice.DEFAULT_CHAR_LIMIT


# --------------------------------------------------------------------------- #
# concat command (pure build)                                                 #
# --------------------------------------------------------------------------- #
def test_build_concat_cmd_uses_concat_demuxer():
    cmd = ffmpeg.build_concat_cmd(Path("list.txt"), Path("out.mp3"))
    assert cmd[0] == ffmpeg.ffmpeg_bin()
    assert "-f" in cmd and "concat" in cmd
    assert "-safe" in cmd
    assert cmd[-1] == "out.mp3"
    assert "libmp3lame" in cmd


# --------------------------------------------------------------------------- #
# synth_voice flow (chunk + concat) with a fake client + faked ffmpeg         #
# --------------------------------------------------------------------------- #
class _FakeVoiceClient(AIClient):
    """Writes a small file per chunk; records the texts it received."""

    capabilities = {Task.GENERATE_VOICE}
    requires_key = False

    def __init__(self, config: ServiceConfig) -> None:
        super().__init__(config)
        self.calls: list[str] = []

    async def is_available(self, ctx: CallContext) -> bool:
        return True

    async def generate_voice(self, req: VoiceRequest, out_path: Path, ctx: CallContext) -> VoiceResult:
        self.calls.append(req.text or "")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"chunk")
        return VoiceResult(out_path=Path(out_path), chars=len(req.text or ""))


class _FakeRegistry:
    def __init__(self, client: AIClient) -> None:
        self._client = client

    async def resolve(self, task, preferred, ctx):
        return self._client


def _series() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="n", topic="t", skill="religion", language="vi",
        target_minutes=5, density="standard",
        providers={"script": "stub-script", "image": "stub-image", "voice": "stub-voice"},
        image_style=ImageStyle(preset_id="p", base_prompt="b"),
        voice=VoiceConfig(provider="stub-voice", voice_id="vid"),
    )


async def test_synth_voice_chunks_and_concats(tmp_path, monkeypatch):
    lo = layout_for(tmp_path)
    lo.voice_dir.mkdir(parents=True)
    lo.script_md.write_text("\n\n===\n\n".join(["a" * 100, "b" * 100, "c" * 100]))

    client = _FakeVoiceClient(
        ServiceConfig("fake", {"tasks": {"generate-voice": {"char_limit": 250}}})
    )

    concat_calls: list[list[Path]] = []

    async def fake_concat(parts, out_path, **kw):
        concat_calls.append(list(parts))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"joined")
        return Path(out_path)

    async def fake_probe(path, **kw):
        return 42.0

    monkeypatch.setattr(voice.ffmpeg, "concat_audio", fake_concat)
    monkeypatch.setattr(voice.ffmpeg, "probe_duration", fake_probe)

    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    outcome = await voice.synth_voice(_series(), lo, ctx, registry=_FakeRegistry(client))

    # 3 sections @100 chars, limit 250 -> 2 chunks
    assert len(client.calls) == 2
    assert len(outcome.parts) == 2
    assert outcome.duration_s == 42.0
    assert concat_calls and len(concat_calls[0]) == 2
    # language propagated to settings
    assert outcome.total_chars > 0


async def test_synth_voice_empty_script_raises(tmp_path, monkeypatch):
    lo = layout_for(tmp_path)
    lo.voice_dir.mkdir(parents=True)
    lo.script_md.write_text("")
    client = _FakeVoiceClient(ServiceConfig("fake", {}))
    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    with pytest.raises(ValueError):
        await voice.synth_voice(_series(), lo, ctx, registry=_FakeRegistry(client))


# --------------------------------------------------------------------------- #
# word-limit chunking (OmniVoice 200-word cap)                                #
# --------------------------------------------------------------------------- #
def test_resolve_word_limit_reads_config_and_none():
    class _C(AIClient):
        capabilities = {Task.GENERATE_VOICE}

    omni = _C(ServiceConfig("omni", {"tasks": {"generate-voice": {"word_limit": 200, "char_limit": 6000}}}))
    assert voice.resolve_word_limit(omni) == 200
    edge = _C(ServiceConfig("edge", {"tasks": {"generate-voice": {"char_limit": 8000}}}))
    assert voice.resolve_word_limit(edge) is None  # absent → None (use char_limit)


def test_split_by_word_limit_packs_small_sections():
    s1 = " ".join(["w"] * 120)
    s2 = " ".join(["x"] * 60)  # 120 + 60 = 180 ≤ 200 → one chunk
    chunks = voice.split_by_word_limit([s1, s2], limit=200)
    assert len(chunks) == 1
    assert voice.count_words(chunks[0]) == 180


def test_split_by_word_limit_splits_oversize_section_by_sentence():
    # 6 sentences × 50 words = 300 words in ONE section; limit 100 → must be split.
    sent = " ".join(["w"] * 49) + " w."  # 50 tokens, ends a sentence
    big = " ".join([sent] * 6)
    chunks = voice.split_by_word_limit([big], limit=100)
    assert len(chunks) > 1  # the oversize section was broken up (not one chunk)
    assert all(voice.count_words(c) <= 100 for c in chunks)  # every chunk under cap


# --------------------------------------------------------------------------- #
# chunk-level resume: a cached chunk is reused (not re-synthesized)           #
# --------------------------------------------------------------------------- #
async def test_synth_voice_reuses_cached_chunk(tmp_path, monkeypatch):
    lo = layout_for(tmp_path)
    lo.voice_dir.mkdir(parents=True)
    # 3 sections @100 chars, char_limit 150 → 3 chunks (each its own).
    lo.script_md.write_text("\n\n===\n\n".join(["a" * 100, "b" * 100, "c" * 100]))
    client = _FakeVoiceClient(
        ServiceConfig("fake", {"tasks": {"generate-voice": {"char_limit": 150}}})
    )

    async def fake_concat(parts, out_path, **kw):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"joined")
        return Path(out_path)

    async def fake_probe(path, **kw):
        return 30.0

    monkeypatch.setattr(voice.ffmpeg, "concat_audio", fake_concat)
    monkeypatch.setattr(voice.ffmpeg, "probe_duration", fake_probe)

    done_idx: list[int] = []
    progress: list[float] = []

    async def on_reuse(i, text, part):
        if i == 1:  # pretend chunk 1 is cached → fetch it into the work folder
            Path(part).parent.mkdir(parents=True, exist_ok=True)
            Path(part).write_bytes(b"cached")
            return True
        return False

    async def on_done(i, text, part):
        done_idx.append(i)

    async def on_prog(f):
        progress.append(f)

    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    outcome = await voice.synth_voice(
        _series(), lo, ctx, registry=_FakeRegistry(client),
        on_chunk_reuse=on_reuse, on_chunk_done=on_done, on_progress=on_prog,
    )

    # Chunk 1 reused → NOT synthesized; only chunks 2 & 3 hit the TTS client.
    assert len(client.calls) == 2
    assert done_idx == [2, 3]  # persisted only the freshly-synthesized chunks
    assert len(outcome.parts) == 3  # all 3 parts present (1 cached + 2 fresh) for concat
    assert progress == sorted(progress) and progress[-1] == 1.0  # bar advances → 100%


async def test_synth_voice_calls_before_chunk_gate(tmp_path, monkeypatch):
    """The pause gate (before_chunk) runs before EACH freshly-synthesized chunk."""
    lo = layout_for(tmp_path)
    lo.voice_dir.mkdir(parents=True)
    lo.script_md.write_text("\n\n===\n\n".join(["a" * 100, "b" * 100]))
    client = _FakeVoiceClient(
        ServiceConfig("fake", {"tasks": {"generate-voice": {"char_limit": 150}}})
    )

    async def fake_concat(parts, out_path, **kw):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"joined")
        return Path(out_path)

    async def fake_probe(path, **kw):
        return 10.0

    monkeypatch.setattr(voice.ffmpeg, "concat_audio", fake_concat)
    monkeypatch.setattr(voice.ffmpeg, "probe_duration", fake_probe)

    gate: list[int] = []

    async def before(i):
        gate.append(i)

    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    await voice.synth_voice(
        _series(), lo, ctx, registry=_FakeRegistry(client), before_chunk=before
    )
    assert gate == [1, 2]  # gated once per chunk, before synthesis
