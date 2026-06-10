"""Skill-wrapper clients (eleven voice, kie image) — no real subprocess.

``run_skill_script`` is patched to return a canned JSON blob, so we assert the
arg/env construction and result mapping rather than launching a child process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import clients.skill_image as skill_image
import clients.skill_voice as skill_voice
from clients.base import (
    CallContext,
    ImageRequest,
    InvalidKeyError,
    ServiceConfig,
    VoiceRequest,
)
from keystore import Cipher, KeyStore
from usage import UsageLogger


def _ctx_with_keys(**refs: str) -> CallContext:
    store = KeyStore(Cipher(b"k" * 32))
    for ref, val in refs.items():
        store.save("u1", ref, val)
    return CallContext(user_id="u1", keys=store, usage=UsageLogger())


_VOICE_CFG = ServiceConfig(
    provider_id="eleven",
    raw={
        "auth": {"type": "key", "key_ref": "elevenlabs", "env": "ELEVENLABS_API_KEY"},
        "tasks": {"generate-voice": {"models": ["eleven_multilingual_v2"]}},
        "pricing": {"generate-voice": {"per_1k_chars": 0.3}},
    },
)
_IMAGE_CFG = ServiceConfig(
    provider_id="kie",
    raw={
        "auth": {"type": "key", "key_ref": "kie", "env": "KIE_API_KEY"},
        "tasks": {"generate-image": {"sizes": ["16:9", "9:16"], "default_size": "16:9"}},
        "pricing": {"generate-image": {"per_image": 0.0}},
    },
)


async def test_skill_voice_builds_args_and_injects_key(monkeypatch, tmp_path: Path):
    captured = {}

    async def fake_run(script, args, env=None, **kw):
        captured["script"] = script
        captured["args"] = args
        captured["env"] = env
        return {"output_path": args[args.index("--output") + 1], "character_count": 5}

    monkeypatch.setattr(skill_voice, "run_skill_script", fake_run)
    client = skill_voice.SkillVoiceClient(_VOICE_CFG)
    ctx = _ctx_with_keys(elevenlabs="sk-eleven")
    out = tmp_path / "v.mp3"

    res = await client.generate_voice(VoiceRequest(voice_id="VID", text="hello"), out, ctx)

    assert captured["script"] == "generate_voice.py"
    assert "--voice-id" in captured["args"] and "VID" in captured["args"]
    assert "--output" in captured["args"]
    # BYOK key injected via env, not args
    assert captured["env"]["ELEVENLABS_API_KEY"] == "sk-eleven"
    assert res.chars == 5
    # usage recorded with cost from pricing
    ev = ctx.usage._sink.events[0]  # type: ignore[attr-defined]
    assert ev.units == 5 and ev.cost == pytest.approx(5 / 1000 * 0.3)


async def test_skill_voice_missing_key_raises(tmp_path: Path):
    client = skill_voice.SkillVoiceClient(_VOICE_CFG)
    ctx = _ctx_with_keys()  # no key
    with pytest.raises(InvalidKeyError):
        await client.generate_voice(VoiceRequest(voice_id="V", text="x"), tmp_path / "v.mp3", ctx)


async def test_skill_voice_writes_temp_file_for_inline_text(monkeypatch, tmp_path: Path):
    seen_text_files = []

    async def fake_run(script, args, env=None, **kw):
        tf = args[args.index("--text-file") + 1]
        seen_text_files.append(Path(tf).read_text(encoding="utf-8"))
        return {"output_path": args[args.index("--output") + 1], "character_count": 11}

    monkeypatch.setattr(skill_voice, "run_skill_script", fake_run)
    client = skill_voice.SkillVoiceClient(_VOICE_CFG)
    ctx = _ctx_with_keys(elevenlabs="sk")
    await client.generate_voice(VoiceRequest(voice_id="V", text="hello world"), tmp_path / "v.mp3", ctx)
    assert seen_text_files == ["hello world"]


async def test_skill_image_builds_args_and_size(monkeypatch, tmp_path: Path):
    captured = {}

    async def fake_run(script, args, env=None, **kw):
        captured["script"] = script
        captured["args"] = args
        captured["env"] = env
        return {"output_path": args[args.index("--output") + 1], "size": "16:9"}

    monkeypatch.setattr(skill_image, "run_skill_script", fake_run)
    client = skill_image.SkillImageClient(_IMAGE_CFG)
    ctx = _ctx_with_keys(kie="sk-kie")
    out = tmp_path / "img.png"

    res = await client.generate_image(ImageRequest(prompt="a temple", size="16:9"), out, ctx)

    assert captured["script"] == "generate_image.py"
    assert "--size" in captured["args"] and "16:9" in captured["args"]
    assert captured["env"]["KIE_API_KEY"] == "sk-kie"
    assert res.count == 1


async def test_skill_image_invalid_size_falls_back_to_default(monkeypatch, tmp_path: Path):
    captured = {}

    async def fake_run(script, args, env=None, **kw):
        captured["args"] = args
        return {"output_path": args[args.index("--output") + 1]}

    monkeypatch.setattr(skill_image, "run_skill_script", fake_run)
    client = skill_image.SkillImageClient(_IMAGE_CFG)
    ctx = _ctx_with_keys(kie="sk")
    await client.generate_image(ImageRequest(prompt="x", size="99:1"), tmp_path / "i.png", ctx)
    # invalid 99:1 -> default_size 16:9
    assert "16:9" in captured["args"]


async def test_skill_image_missing_key_raises(tmp_path: Path):
    client = skill_image.SkillImageClient(_IMAGE_CFG)
    ctx = _ctx_with_keys()
    with pytest.raises(InvalidKeyError):
        await client.generate_image(ImageRequest(prompt="x"), tmp_path / "i.png", ctx)
