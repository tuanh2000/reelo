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
