"""Stub clients produce valid, parseable outputs with no network (Module 3).

These back Module 1 & 2 tests, so the contract that matters is: EchoScriptClient
returns JSON honouring the request schema, SilentVoiceClient writes a real .mp3,
PlaceholderImageClient writes a real .png.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path


from clients.base import (
    CallContext,
    ImageRequest,
    ScriptRequest,
    ServiceConfig,
    Task,
    VoiceRequest,
)
from clients.stub import EchoScriptClient, PlaceholderImageClient, SilentVoiceClient
from keystore import Cipher, KeyStore
from usage import UsageLogger


def _ctx() -> CallContext:
    return CallContext(user_id="u1", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())


def _cfg(provider_id: str) -> ServiceConfig:
    return ServiceConfig(provider_id=provider_id, raw={"auth": {"type": "none"}, "tasks": {}})


# A representative Module-1-style segment schema.
_SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "narration": {"type": "string"},
                    "image_prompt": {"type": "string"},
                },
            },
        },
        "youtube_title": {"type": "string"},
    },
}


async def test_echo_script_returns_schema_valid_json():
    client = EchoScriptClient(_cfg("stub-script"))
    req = ScriptRequest(messages=[{"role": "user", "content": "hi"}], json_schema=_SEGMENT_SCHEMA)
    result = await client.write_script(req, _ctx())
    data = json.loads(result.text)  # must parse
    assert "segments" in data and isinstance(data["segments"], list)
    assert data["segments"]  # non-empty
    assert "narration" in data["segments"][0]
    assert "youtube_title" in data
    assert result.usage and result.usage["total_tokens"] == 30


async def test_echo_script_without_schema_echoes_last_turn():
    client = EchoScriptClient(_cfg("stub-script"))
    req = ScriptRequest(messages=[{"role": "user", "content": "hello world"}])
    result = await client.write_script(req, _ctx())
    assert "hello world" in result.text


async def test_echo_records_usage():
    ctx = _ctx()
    client = EchoScriptClient(_cfg("stub-script"))
    await client.write_script(ScriptRequest(messages=[{"role": "user", "content": "x"}]), ctx)
    assert len(ctx.usage._sink.events) == 1  # type: ignore[attr-defined]


async def test_silent_voice_writes_valid_mp3(tmp_path: Path):
    client = SilentVoiceClient(_cfg("stub-voice"))
    out = tmp_path / "voice" / "a.mp3"
    res = await client.generate_voice(VoiceRequest(voice_id="v", text="hello"), out, _ctx())
    assert res.out_path == out
    assert out.exists() and out.stat().st_size > 0
    # MPEG frame sync: first 11 bits set (0xFF 0xFB...)
    head = out.read_bytes()[:2]
    assert head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    assert res.chars == len("hello")


async def test_silent_voice_reads_text_file(tmp_path: Path):
    src = tmp_path / "script.txt"
    src.write_text("xin chao the gioi", encoding="utf-8")
    client = SilentVoiceClient(_cfg("stub-voice"))
    out = tmp_path / "a.mp3"
    res = await client.generate_voice(VoiceRequest(voice_id="v", text_file=src), out, _ctx())
    assert res.chars == len("xin chao the gioi")


async def test_placeholder_image_writes_valid_png(tmp_path: Path):
    client = PlaceholderImageClient(_cfg("stub-image"))
    out = tmp_path / "img" / "001.png"
    res = await client.generate_image(ImageRequest(prompt="a temple", size="16:9"), out, _ctx())
    assert res.count == 1
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG signature
    # IHDR width/height are 64x36 for 16:9
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (64, 36)


async def test_placeholder_image_dims_per_aspect(tmp_path: Path):
    client = PlaceholderImageClient(_cfg("stub-image"))
    out = tmp_path / "p.png"
    await client.generate_image(ImageRequest(prompt="x", size="9:16"), out, _ctx())
    width, height = struct.unpack(">II", out.read_bytes()[16:24])
    assert (width, height) == (36, 64)


async def test_stub_clients_are_keyless_and_available():
    for cls, pid in (
        (EchoScriptClient, "stub-script"),
        (SilentVoiceClient, "stub-voice"),
        (PlaceholderImageClient, "stub-image"),
    ):
        client = cls(_cfg(pid))
        assert client.requires_key is False
        assert await client.is_available(_ctx()) is True
        assert await client.validate_key(_ctx()) is True


def test_stub_capabilities():
    assert EchoScriptClient(_cfg("s")).supports(Task.WRITE_SCRIPT)
    assert SilentVoiceClient(_cfg("s")).supports(Task.GENERATE_VOICE)
    assert PlaceholderImageClient(_cfg("s")).supports(Task.GENERATE_IMAGE)
