"""Validate the cross-module Pydantic contracts and crypto."""

from __future__ import annotations

import base64

import pytest

from clients.base import (
    AIClient,
    CallContext,
    NotSupportedError,
    ScriptRequest,
    ServiceConfig,
    Task,
)
from keystore import Cipher, KeyStore
from models.jobs import GenJob
from models.spec import (
    EpisodeSpec,
    ImageStyle,
    SegmentSpec,
    SeriesSpec,
    VoiceConfig,
)
from usage import UsageLogger, compute_cost


def _series_spec() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1",
        name="Ancient Religions",
        topic="religion & history",
        skill="religion",
        language="vi",
        target_minutes=10,
        density="standard",
        providers={"script": "claude", "image": "kie", "voice": "edge"},
        image_style=ImageStyle(preset_id="cinematic", base_prompt="cinematic still"),
        voice=VoiceConfig(provider="edge", voice_id="vi-VN-HoaiMyNeural"),
        episodes=[EpisodeSpec(episode_id="e1", title="Origins", order=1)],
    )


def test_series_spec_round_trips():
    spec = _series_spec()
    dumped = spec.model_dump()
    restored = SeriesSpec.model_validate(dumped)
    assert restored == spec
    # JSONB shape is a plain dict
    assert isinstance(dumped["image_style"], dict)
    assert restored.episodes[0].segments == []


def test_segment_spec_index_is_one_based():
    seg = SegmentSpec(index=1, narration="xin chao", image_prompt="a temple", image_label="temple")
    assert seg.index == 1
    with pytest.raises(Exception):
        SegmentSpec(index=0, narration="x", image_prompt="y", image_label="z")


def test_genjob_matches_ui_shape():
    job = GenJob(id="j1", name="Voice", icon="mic", state="running", progress=42)
    d = job.model_dump()
    # `stderr` is the copyable per-job error detail (None unless state == "error").
    # `preview_url` is the signed image URL (image jobs only, once done) for the
    # produce-screen live preview. Both default to None.
    assert set(d.keys()) == {
        "id", "name", "icon", "state", "progress", "stderr", "preview_url"
    }
    assert d["state"] == "running"
    assert d["stderr"] is None
    assert d["preview_url"] is None


def test_aes_gcm_round_trip():
    cipher = Cipher(base64.b64decode(base64.b64encode(b"k" * 32)))
    store = KeyStore(cipher)
    assert store.has("u1", "elevenlabs") is False
    store.save("u1", "elevenlabs", "sk-secret-123")
    assert store.has("u1", "elevenlabs") is True
    assert store.get("u1", "elevenlabs") == "sk-secret-123"
    # AAD binding: a different user cannot read another's record
    assert store.get("u2", "elevenlabs") is None
    # as_env maps env-var -> key_ref, only present keys included
    env = store.as_env("u1", {"ELEVENLABS_API_KEY": "elevenlabs", "KIE_API_KEY": "kie"})
    assert env == {"ELEVENLABS_API_KEY": "sk-secret-123"}


def test_cipher_rejects_bad_key_size():
    with pytest.raises(ValueError):
        Cipher(b"short")


def test_compute_cost_per_task():
    pricing = {
        "write-script": {"per_1k_input": 1.0, "per_1k_output": 2.0},
        "generate-voice": {"per_1k_chars": 0.3},
        "generate-image": {"per_image": 0.04},
    }
    assert compute_cost("write-script", 1000, pricing) == pytest.approx(2.0)
    assert compute_cost("generate-voice", 2000, pricing) == pytest.approx(0.6)
    assert compute_cost("generate-image", 5, pricing) == pytest.approx(0.2)
    assert compute_cost("generate-image", 5, {}) is None


def test_usage_logger_records():
    logger = UsageLogger()
    logger.record("u1", "eleven", "generate-voice", 1500, 0.45)
    # default in-memory sink keeps events
    assert len(logger._sink.events) == 1  # type: ignore[attr-defined]
    assert logger._sink.events[0].user_id == "u1"  # type: ignore[attr-defined]


def test_aiclient_abc_defaults_and_dispatch():
    """A minimal subclass derives capabilities/requires_key from config and
    raises NotSupportedError for unimplemented tasks."""

    class StubClient(AIClient):
        async def write_script(self, req, ctx):  # type: ignore[override]
            return type("R", (), {"text": "ok"})()

    cfg = ServiceConfig(
        provider_id="stub",
        raw={
            "cost_tier": "free",
            "auth": {"type": "key", "key_ref": "stub"},
            "tasks": {"write-script": {}},
        },
    )
    client = StubClient(cfg)
    assert client.supports(Task.WRITE_SCRIPT) is True
    assert client.supports(Task.GENERATE_IMAGE) is False
    assert client.requires_key is True
    assert client.cost_tier == "free"


@pytest.mark.anyio
async def test_aiclient_unsupported_raises():
    class StubClient(AIClient):
        capabilities = {Task.WRITE_SCRIPT}
        cost_tier = "free"
        requires_key = False

    cfg = ServiceConfig(provider_id="stub", raw={"auth": {"type": "none"}})
    client = StubClient(cfg)
    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    with pytest.raises(NotSupportedError):
        await client.generate_image(ScriptRequest(messages=[]), None, ctx)  # type: ignore[arg-type]


@pytest.fixture
def anyio_backend():
    return "asyncio"
