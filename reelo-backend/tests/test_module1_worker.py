"""worker.tasks.generate_script wiring: loads spec, generates, persists, flushes.

DB + registry are faked; the script provider is the test FakeSegmentClient.
"""

from __future__ import annotations

import contextlib

import worker.tasks as tasks
from clients.base import CallContext
from keystore import Cipher, KeyStore
from models.spec import EpisodeSpec, ImageStyle, SeriesSpec, VoiceConfig
from tests.test_module1_episode_script import FakeSegmentClient, _registry_with
from usage import UsageLogger


def _spec() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="Faiths", topic="religion", skill="religion",
        language="vi", target_minutes=5, density="standard",
        providers={"script": "fake", "image": "kie", "voice": "edge"},
        image_style=ImageStyle(preset_id="painterly-devotional", base_prompt="b"),
        voice=VoiceConfig(provider="edge", voice_id="v"),
        episodes=[EpisodeSpec(episode_id="e1", title="Origins", order=1, desc="d")],
    )


class _FakeEpisodeRepo:
    """Captures set_script_state calls so tests can assert the running→done flow."""

    calls: list[tuple] = []

    def __init__(self, session):
        pass

    async def set_script_state(self, user_id, episode_id, status, error=None):
        type(self).calls.append((status, error))
        return object()


async def test_generate_script_task_persists_scripted(monkeypatch):
    spec = _spec()
    saved: dict = {}
    flushed: list[bool] = []

    async def fake_build_ctx(ctx, user_id):
        return CallContext(user_id=user_id, keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())

    async def fake_flush(call_ctx):
        flushed.append(True)
        return 0

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield object()

    async def fake_find(session, user_id, episode_id):
        return object(), spec, spec.episodes[0]

    async def fake_update(session, user_id, series_id, updated):
        saved["episode"] = updated
        return spec

    _FakeEpisodeRepo.calls = []
    # Patch the registry the episode_script module resolves through.
    monkeypatch.setattr(tasks, "build_call_context", fake_build_ctx)
    monkeypatch.setattr(tasks, "flush_call_context_usage", fake_flush)
    monkeypatch.setattr(tasks, "session_scope", fake_scope)
    monkeypatch.setattr(tasks, "EpisodeRepo", _FakeEpisodeRepo)

    import module1.persistence as persistence

    monkeypatch.setattr(persistence, "find_series_for_episode", fake_find)
    monkeypatch.setattr(persistence, "update_episode_in_series", fake_update)

    import module1.episode_script as episode_script

    monkeypatch.setattr(episode_script, "get_registry", lambda: _registry_with(FakeSegmentClient))

    result = await tasks.generate_script({}, "u1", "e1")

    assert result["status"] == "scripted"
    assert result["segments"] == 9
    assert saved["episode"].status == "scripted"
    assert len(saved["episode"].segments) == 9
    assert flushed == [True]
    # running on entry, done on success (no error recorded)
    assert _FakeEpisodeRepo.calls == [("running", None), ("done", None)]


async def test_generate_script_task_missing_episode_flushes_and_raises(monkeypatch):
    flushed: list[bool] = []

    async def fake_build_ctx(ctx, user_id):
        return CallContext(user_id=user_id, keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())

    async def fake_flush(call_ctx):
        flushed.append(True)
        return 0

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield object()

    async def fake_find(session, user_id, episode_id):
        return None

    _FakeEpisodeRepo.calls = []
    monkeypatch.setattr(tasks, "build_call_context", fake_build_ctx)
    monkeypatch.setattr(tasks, "flush_call_context_usage", fake_flush)
    monkeypatch.setattr(tasks, "session_scope", fake_scope)
    monkeypatch.setattr(tasks, "EpisodeRepo", _FakeEpisodeRepo)

    import module1.persistence as persistence

    monkeypatch.setattr(persistence, "find_series_for_episode", fake_find)

    import pytest

    with pytest.raises(ValueError):
        await tasks.generate_script({}, "u1", "missing")
    assert flushed == [True]  # flush still runs in finally
    # The failure is recorded as an error state (not silently swallowed).
    assert _FakeEpisodeRepo.calls and _FakeEpisodeRepo.calls[-1][0] == "error"
    assert "not found" in _FakeEpisodeRepo.calls[-1][1]
