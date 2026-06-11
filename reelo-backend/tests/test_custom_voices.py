"""Shared OmniVoice voice-clone library — /voices catalog + apply-to-series.

Covers the cross-tenant sharing requirement: a voice user A names is browsable
and reusable by user B without re-entering the reference clip. DB + storage are
faked in-memory (one shared store wired into both routers) so the routing,
ownership, and spec-mutation logic is exercised without Postgres. Tests that
build a real wav (the create path normalizes via ffmpeg) skip when ffmpeg is
absent.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from pathlib import Path

import pytest

from module2 import ffmpeg

requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg.ffmpeg_available(), reason="ffmpeg/ffprobe not installed"
)


def _make_wav(path: Path, *, seconds: float = 5.0) -> bytes:
    argv = [
        ffmpeg.ffmpeg_bin(), "-y",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-ar", "24000", "-ac", "1", str(path),
    ]
    subprocess.run(argv, check=True, capture_output=True)
    return path.read_bytes()


# --------------------------------------------------------------------------- #
# In-memory fakes (one shared store wired into the voices + series routers)    #
# --------------------------------------------------------------------------- #
class _FakeVoiceRow:
    def __init__(self, **kw):
        self.id = kw["id"]
        self.created_by_user_id = kw["created_by_user_id"]
        self.name = kw["name"]
        self.audio_key = kw["audio_key"]
        self.transcript = kw["transcript"]
        self.language = kw.get("language")
        self.duration_s = kw.get("duration_s")
        self.created_at = kw.get("created_at") or _dt.datetime(
            2026, 6, 11, tzinfo=_dt.timezone.utc
        )


class _FakeCustomVoiceRepo:
    def __init__(self, store):
        self.store = store  # dict id -> _FakeVoiceRow (insertion-ordered)

    async def list_all(self):
        return list(reversed(list(self.store.values())))  # newest first

    async def get(self, voice_id):
        return self.store.get(voice_id)

    async def create(self, *, voice_id, created_by_user_id, name, audio_key,
                     transcript, language, duration_s):
        row = _FakeVoiceRow(
            id=voice_id, created_by_user_id=created_by_user_id, name=name,
            audio_key=audio_key, transcript=transcript, language=language,
            duration_s=duration_s,
        )
        self.store[voice_id] = row
        return row

    async def delete_owned(self, voice_id, user_id):
        row = self.store.get(voice_id)
        if row is None:
            return "missing"
        if row.created_by_user_id != user_id:
            return "forbidden"
        del self.store[voice_id]
        return "deleted"


class _SeriesRow:
    def __init__(self, spec):
        self.spec_json = spec.model_dump()
        self.id = spec.series_id


class _FakeSeriesRepo:
    def __init__(self, store):
        self.store = store  # series_id -> _SeriesRow

    async def get(self, user_id, series_id):
        return self.store.get(series_id)


class _FakeStorage:
    def __init__(self, store):
        self.store = store

    async def put(self, key, data, **kw):
        self.store["puts"][key] = data
        return key

    async def signed_url(self, key, *, expires_in=None):
        return f"http://files.test/{key}"

    async def delete(self, key):
        self.store["puts"].pop(key, None)


def _spec(series_id="s1"):
    from models.spec import ImageStyle, SeriesSpec, VoiceConfig

    return SeriesSpec(
        series_id=series_id, name="n", topic="t", skill="religion", language="vi",
        target_minutes=5, density="standard",
        providers={"script": "stub-script", "image": "stub-image", "voice": "edge"},
        image_style=ImageStyle(preset_id="p", base_prompt="b"),
        voice=VoiceConfig(provider="edge", voice_id="vi-VN"),
    )


@pytest.fixture()
def vc_client(monkeypatch):
    from fastapi.testclient import TestClient

    import web.routers.series as series_router
    import web.routers.voices as voices_router
    from web.app import create_app
    from web.deps import get_current_user, get_db

    voices = {}          # voice_id -> _FakeVoiceRow
    series = {}          # series_id -> _SeriesRow
    storage = {"puts": {}}
    user = {"id": "u_a"}

    monkeypatch.setattr(voices_router, "CustomVoiceRepo", lambda s: _FakeCustomVoiceRepo(voices))
    monkeypatch.setattr(voices_router, "get_storage", lambda: _FakeStorage(storage))
    monkeypatch.setattr(series_router, "CustomVoiceRepo", lambda s: _FakeCustomVoiceRepo(voices))
    monkeypatch.setattr(series_router, "SeriesRepo", lambda s: _FakeSeriesRepo(series))
    monkeypatch.setattr(series_router, "get_storage", lambda: _FakeStorage(storage))

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user["id"]

    class _FakeDb:
        async def flush(self):
            return None

    async def _fake_db():
        yield _FakeDb()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.voices = voices            # type: ignore[attr-defined]
    client.series = series            # type: ignore[attr-defined]
    client.storage = storage          # type: ignore[attr-defined]
    client.as_user = lambda uid: user.__setitem__("id", uid)  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def _seed_voice(client, *, voice_id="voice_seed", owner="u_a", name="Giọng A",
                language="vi"):
    client.voices[voice_id] = _FakeVoiceRow(
        id=voice_id, created_by_user_id=owner, name=name,
        audio_key=f"custom-voices/{voice_id}/sample.wav",
        transcript="đây là câu mẫu", language=language, duration_s=5.0,
    )
    return voice_id


# --------------------------------------------------------------------------- #
# Create + list                                                               #
# --------------------------------------------------------------------------- #
@requires_ffmpeg
def test_create_voice_adds_to_shared_library(vc_client, tmp_path):
    wav = _make_wav(tmp_path / "u.wav", seconds=5.0)
    resp = vc_client.post(
        "/voices",
        files={"audio": ("u.wav", wav, "audio/wav")},
        data={"name": "Giọng của tôi", "transcript": "đây là câu mẫu", "language": "vi"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Giọng của tôi"
    assert body["language"] == "vi"
    assert body["is_owner"] is True
    assert 4.0 < body["duration_s"] < 6.0
    # The reference clip was stored under the GLOBAL custom-voices/ prefix as wav.
    key = f"custom-voices/{body['id']}/sample.wav"
    assert vc_client.storage["puts"][key][:4] == b"RIFF"
    # It now shows up in the shared catalog.
    listed = vc_client.get("/voices").json()["voices"]
    assert [v["id"] for v in listed] == [body["id"]]


def test_create_voice_requires_name(vc_client):
    resp = vc_client.post(
        "/voices",
        files={"audio": ("u.wav", b"RIFFxx", "audio/wav")},
        data={"name": "  ", "transcript": "x"},
    )
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"]


def test_list_voices_cross_tenant_is_owner_flag(vc_client):
    _seed_voice(vc_client, voice_id="v1", owner="u_a", name="Giọng A")

    # Another tenant sees the voice but is not its owner.
    vc_client.as_user("u_b")
    voices = vc_client.get("/voices").json()["voices"]
    assert len(voices) == 1
    assert voices[0]["id"] == "v1"
    assert voices[0]["is_owner"] is False
    assert voices[0]["transcript"] == "đây là câu mẫu"

    # The creator sees is_owner=True.
    vc_client.as_user("u_a")
    assert vc_client.get("/voices").json()["voices"][0]["is_owner"] is True


# --------------------------------------------------------------------------- #
# Apply a library voice to a series (cross-tenant reuse)                       #
# --------------------------------------------------------------------------- #
def test_apply_custom_voice_to_series_cross_tenant(vc_client):
    _seed_voice(vc_client, voice_id="v1", owner="u_a")
    # User B owns the series and reuses user A's voice.
    vc_client.series["s1"] = _SeriesRow(_spec("s1"))
    vc_client.as_user("u_b")

    resp = vc_client.post("/series/s1/voice/custom", json={"voice_id": "v1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["audio_key"] == "custom-voices/v1/sample.wav"
    assert body["voice"]["provider"] == "omnivoice"
    assert body["voice"]["mode"] == "clone"
    assert body["voice"]["voice_sample"]["audio_key"] == "custom-voices/v1/sample.wav"
    assert body["voice"]["voice_sample"]["transcript"] == "đây là câu mẫu"
    # Series spec was mutated: providers.voice flipped to omnivoice.
    assert vc_client.series["s1"].spec_json["providers"]["voice"] == "omnivoice"


def test_apply_custom_voice_404_missing_voice(vc_client):
    vc_client.series["s1"] = _SeriesRow(_spec("s1"))
    resp = vc_client.post("/series/s1/voice/custom", json={"voice_id": "nope"})
    assert resp.status_code == 404


def test_apply_custom_voice_404_missing_series(vc_client):
    _seed_voice(vc_client, voice_id="v1")
    resp = vc_client.post("/series/nope/voice/custom", json={"voice_id": "v1"})
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Preview + delete (creator-only)                                             #
# --------------------------------------------------------------------------- #
def test_preview_voice_returns_url(vc_client):
    _seed_voice(vc_client, voice_id="v1")
    resp = vc_client.get("/voices/v1/preview")
    assert resp.status_code == 200
    assert resp.json()["url"].endswith("custom-voices/v1/sample.wav")


def test_preview_voice_404(vc_client):
    assert vc_client.get("/voices/nope/preview").status_code == 404


def test_delete_voice_creator_only(vc_client):
    _seed_voice(vc_client, voice_id="v1", owner="u_a")

    # A non-creator cannot delete.
    vc_client.as_user("u_b")
    assert vc_client.delete("/voices/v1").status_code == 403
    assert "v1" in vc_client.voices

    # The creator can.
    vc_client.as_user("u_a")
    assert vc_client.delete("/voices/v1").status_code == 204
    assert "v1" not in vc_client.voices


def test_delete_voice_404(vc_client):
    assert vc_client.delete("/voices/nope").status_code == 404
