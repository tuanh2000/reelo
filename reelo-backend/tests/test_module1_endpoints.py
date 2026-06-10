"""Module 1 web endpoints: /wizard/message, /wizard/approve, /series CRUD,
/episodes/{id}/script, /style/infer.

No live DB/Redis: persistence helpers + enqueue + the AI call context are faked,
and the wizard's AI provider is the keyless EchoScriptClient stub.
"""

from __future__ import annotations

import struct
import zlib

import pytest

import web.routers.episodes as episodes_router
import web.routers.series as series_router
import web.routers.wizard as wizard_router
from clients.base import CallContext
from keystore import Cipher, KeyStore
from models.spec import EpisodeSpec, ImageStyle, SeriesSpec, VoiceConfig
from usage import UsageLogger
from web.deps import get_current_user, get_db

FAKE_USER_ID = "u_test"


# --------------------------------------------------------------------------- #
# In-memory series store (keyed by series_id) + spec helpers                  #
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self) -> None:
        self.series: dict[str, SeriesSpec] = {}
        # episode_id -> object with .paths (signed-asset source for GET /episodes/{id})
        self.episodes: dict[str, object] = {}

    def episode_index(self) -> dict[str, str]:
        return {ep.episode_id: sid for sid, sp in self.series.items() for ep in sp.episodes}


@pytest.fixture()
def store():
    return Store()


@pytest.fixture()
def m1_client(store, monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    async def fake_call_ctx(ctx, user_id):
        return CallContext(user_id=user_id, keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())

    async def fake_flush(call_ctx):
        return 0

    # ---- persistence fakes (operate on the in-memory Store) ----
    async def fake_save(session, user_id, spec: SeriesSpec):
        store.series[spec.series_id] = spec
        return object()

    async def fake_find_for_episode(session, user_id, episode_id):
        sid = store.episode_index().get(episode_id)
        if sid is None:
            return None
        sp = store.series[sid]
        ep = next(e for e in sp.episodes if e.episode_id == episode_id)
        return _Row(sp), sp, ep

    class FakeEpisodeRepo:
        def __init__(self, session):
            pass

        async def get(self, user_id, episode_id):
            return store.episodes.get(episode_id)

        async def set_script_state(self, user_id, episode_id, status, error=None):
            store.script_state = (status, error)
            return store.episodes.get(episode_id)

        @staticmethod
        def script_state(paths):
            p = paths or {}
            st = p.get("script_status")
            return (st if isinstance(st, str) else None), p.get("script_error")

    class FakeStorage:
        async def signed_url(self, key, **kw):
            return f"https://signed/{key}"

    class FakeSeriesRepo:
        def __init__(self, session):
            pass

        async def list_for_user(self, user_id):
            return [_Row(sp) for sp in store.series.values()]

        async def get(self, user_id, series_id):
            sp = store.series.get(series_id)
            return _Row(sp) if sp else None

        async def rename(self, user_id, series_id, name):
            sp = store.series.get(series_id)
            if sp is None:
                return None
            renamed = sp.model_copy(update={"name": name})
            store.series[series_id] = renamed
            return _Row(renamed)

    def fake_spec_from_row(row):
        return row.spec

    async def fake_enqueue(function, *args, **kwargs):
        store.enqueued = (function, args)
        return "job-123"

    # wizard router
    monkeypatch.setattr(wizard_router, "build_call_context", fake_call_ctx)
    monkeypatch.setattr(wizard_router, "flush_call_context_usage", fake_flush)
    monkeypatch.setattr(wizard_router, "save_series_spec", fake_save)
    # episodes router
    monkeypatch.setattr(episodes_router, "find_series_for_episode", fake_find_for_episode)
    monkeypatch.setattr(episodes_router, "enqueue_job", fake_enqueue)
    monkeypatch.setattr(episodes_router, "EpisodeRepo", FakeEpisodeRepo)
    monkeypatch.setattr(episodes_router, "get_storage", lambda: FakeStorage())
    # series router
    monkeypatch.setattr(series_router, "save_series_spec", fake_save)
    monkeypatch.setattr(series_router, "SeriesRepo", FakeSeriesRepo)
    monkeypatch.setattr(series_router, "spec_from_row", fake_spec_from_row)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.store = store  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


class _Row:
    def __init__(self, spec):
        self.spec = spec
        self.id = spec.series_id if spec else None


def _config_body():
    return {
        "skill": "religion",
        "language": "vi",
        "target_minutes": 10,
        "density": "standard",
        "aspect": "16:9",
        "providers": {"script": "stub-script", "image": "kie", "voice": "edge"},
        "voice": {"provider": "edge", "voice_id": "v"},
        "image_style": {
            "preset_id": "painterly-devotional",
            "base_prompt": "base",
            "palette": ["#111"],
            "description": "d",
            "aspect": "16:9",
        },
    }


# --------------------------------------------------------------------------- #
# /wizard/message                                                             #
# --------------------------------------------------------------------------- #
def test_wizard_message_returns_reply(m1_client):
    resp = m1_client.post(
        "/wizard/message",
        json={"idea": "a series about ancient religions", "history": []},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "reply" in body
    # stub echoes the idea; no outline block → outline omitted/None
    assert body["outline"] is None


def test_wizard_message_requires_auth():
    from fastapi.testclient import TestClient

    from web.app import create_app

    anon = TestClient(create_app())
    assert anon.post("/wizard/message", json={"idea": "x", "history": []}).status_code == 401


# --------------------------------------------------------------------------- #
# /wizard/approve  → persist shell                                            #
# --------------------------------------------------------------------------- #
def test_wizard_approve_persists_shell(m1_client):
    resp = m1_client.post(
        "/wizard/approve",
        json={
            "name": "Ancient Faiths",
            "topic": "religion & history",
            "outline": [
                {"id": "w1", "title": "Origins", "desc": "how it began", "pick": True},
                {"id": "w2", "title": "Skipped", "desc": "no", "pick": False},
            ],
            "config": _config_body(),
        },
    )
    assert resp.status_code == 200, resp.text
    series = resp.json()["series"]
    assert series["skill"] == "religion"
    assert len(series["episodes"]) == 1  # only picked
    assert series["episodes"][0]["status"] == "draft"
    assert series["episodes"][0]["segments"] == []
    # persisted to the store
    assert series["series_id"] in m1_client.store.series  # type: ignore[attr-defined]


def test_wizard_approve_snapshots_account_providers(m1_client, monkeypatch):
    """Providers come from account settings, NOT the request config."""
    import web.routers.wizard as wizard_router

    async def fake_account(db, user_id):
        return {"script": "claude", "image": "kie", "voice": "eleven"}

    monkeypatch.setattr(wizard_router, "_account_providers", fake_account)

    body = _config_body()
    # Request providers are deliberately bogus; they must be ignored.
    body["providers"] = {"script": "BOGUS", "image": "BOGUS", "voice": "BOGUS"}
    resp = m1_client.post(
        "/wizard/approve",
        json={
            "name": "Faiths",
            "topic": "religion",
            "outline": [{"id": "w1", "title": "Origins", "desc": "", "pick": True}],
            "config": body,
        },
    )
    assert resp.status_code == 200, resp.text
    providers = resp.json()["series"]["providers"]
    assert providers == {"script": "claude", "image": "kie", "voice": "eleven"}


def test_wizard_approve_snapshots_omnivoice_clone_sample(m1_client, monkeypatch):
    """OmniVoice voice provider → series.voice is clone mode with the account sample."""
    import web.routers.wizard as wizard_router

    async def fake_account(db, user_id):
        return {"script": "claude", "image": "kie", "voice": "omnivoice"}

    async def fake_sample(db, user_id):
        return {"audio_key": "voice-samples/u_test/sample.wav", "transcript": "xin chào", "language": "vi"}

    monkeypatch.setattr(wizard_router, "_account_providers", fake_account)
    monkeypatch.setattr(wizard_router, "_account_voice_sample", fake_sample)

    resp = m1_client.post(
        "/wizard/approve",
        json={
            "name": "Faiths",
            "topic": "religion",
            "outline": [{"id": "w1", "title": "Origins", "desc": "", "pick": True}],
            "config": _config_body(),  # request voice is edge/preset — must be overridden
        },
    )
    assert resp.status_code == 200, resp.text
    voice = resp.json()["series"]["voice"]
    assert voice["provider"] == "omnivoice"
    assert voice["mode"] == "clone"
    assert voice["voice_id"] == ""
    assert voice["voice_sample"]["audio_key"] == "voice-samples/u_test/sample.wav"
    assert voice["voice_sample"]["transcript"] == "xin chào"
    assert voice["voice_sample"]["language"] == "vi"


def test_wizard_approve_omnivoice_without_sample_is_clone_no_sample(m1_client, monkeypatch):
    """OmniVoice with no uploaded sample → clone mode, voice_sample None (not blocked)."""
    import web.routers.wizard as wizard_router

    async def fake_account(db, user_id):
        return {"script": "claude", "image": "kie", "voice": "omnivoice"}

    async def no_sample(db, user_id):
        return None

    monkeypatch.setattr(wizard_router, "_account_providers", fake_account)
    monkeypatch.setattr(wizard_router, "_account_voice_sample", no_sample)

    resp = m1_client.post(
        "/wizard/approve",
        json={
            "name": "Faiths",
            "topic": "religion",
            "outline": [{"id": "w1", "title": "Origins", "desc": "", "pick": True}],
            "config": _config_body(),
        },
    )
    assert resp.status_code == 200, resp.text
    voice = resp.json()["series"]["voice"]
    assert voice["provider"] == "omnivoice"
    assert voice["mode"] == "clone"
    assert voice["voice_sample"] is None


def test_wizard_approve_409_in_prod_when_unconfigured(m1_client, monkeypatch):
    """Prod with no account script/image provider → 409 (UI gate)."""
    import web.routers.wizard as wizard_router
    from config import get_settings

    async def empty(db, user_id):
        return {"script": None, "image": None, "voice": "edge"}

    monkeypatch.setattr(wizard_router, "_account_providers", empty)
    monkeypatch.setattr(get_settings(), "env", "prod")  # is_prod -> True

    resp = m1_client.post(
        "/wizard/approve",
        json={
            "name": "Faiths",
            "topic": "religion",
            "outline": [{"id": "w1", "title": "Origins", "desc": "", "pick": True}],
            "config": _config_body(),
        },
    )
    assert resp.status_code == 409


def test_wizard_message_409_in_prod_when_no_script_provider(m1_client, monkeypatch):
    import web.routers.wizard as wizard_router
    from config import get_settings

    async def empty(db, user_id):
        return {"script": None, "image": None, "voice": "edge"}

    monkeypatch.setattr(wizard_router, "_account_providers", empty)
    monkeypatch.setattr(get_settings(), "env", "prod")

    resp = m1_client.post(
        "/wizard/message", json={"idea": "ancient religions", "history": []}
    )
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# /series CRUD                                                                 #
# --------------------------------------------------------------------------- #
def _seed_series(store) -> SeriesSpec:
    spec = SeriesSpec(
        series_id="s1", name="Faiths", topic="religion", skill="religion",
        language="vi", target_minutes=10, density="standard",
        providers={"script": "stub-script", "image": "kie", "voice": "edge"},
        image_style=ImageStyle(preset_id="painterly-devotional", base_prompt="b"),
        voice=VoiceConfig(provider="edge", voice_id="v"),
        episodes=[EpisodeSpec(episode_id="e1", title="Origins", order=1)],
    )
    store.series["s1"] = spec
    return spec


def test_list_series(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    body = m1_client.get("/series").json()
    assert len(body["series"]) == 1
    assert body["series"][0]["series_id"] == "s1"


def test_create_series(m1_client):
    spec = _seed_series(Store())
    resp = m1_client.post("/series", json={"series": spec.model_dump()})
    assert resp.status_code == 200, resp.text
    assert "s1" in m1_client.store.series  # type: ignore[attr-defined]


def test_update_series_404_when_missing(m1_client):
    spec = _seed_series(Store())
    resp = m1_client.put("/series/nope", json={"series": spec.model_dump()})
    assert resp.status_code == 404


def test_update_series_uses_path_id(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    spec = m1_client.store.series["s1"]  # type: ignore[attr-defined]
    updated = spec.model_copy(update={"name": "Renamed"})
    resp = m1_client.put("/series/s1", json={"series": updated.model_dump()})
    assert resp.status_code == 200
    assert resp.json()["series"]["name"] == "Renamed"


def test_rename_series_updates_name(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    resp = m1_client.patch("/series/s1", json={"name": "  New Name  "})
    assert resp.status_code == 200, resp.text
    # trimmed + returned in the listSeries item shape ({series: SeriesSpec})
    assert resp.json()["series"]["name"] == "New Name"
    assert m1_client.store.series["s1"].name == "New Name"  # type: ignore[attr-defined]


def test_rename_series_404_when_missing(m1_client):
    resp = m1_client.patch("/series/nope", json={"name": "X"})
    assert resp.status_code == 404


def test_rename_series_rejects_empty(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    assert m1_client.patch("/series/s1", json={"name": "   "}).status_code == 422


def test_rename_series_rejects_too_long(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    assert m1_client.patch("/series/s1", json={"name": "x" * 121}).status_code == 422


# --------------------------------------------------------------------------- #
# /episodes/{id}/script  → enqueue (idempotent)                               #
# --------------------------------------------------------------------------- #
def test_episode_script_enqueues_when_empty(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    resp = m1_client.post("/episodes/e1/script")
    assert resp.status_code == 200, resp.text
    assert resp.json()["episode"]["episode_id"] == "e1"
    assert m1_client.store.enqueued[0] == "generate_script"  # type: ignore[attr-defined]
    assert m1_client.store.enqueued[1] == (FAKE_USER_ID, "e1")  # type: ignore[attr-defined]


def test_episode_script_idempotent_when_scripted(m1_client):
    spec = _seed_series(m1_client.store)  # type: ignore[attr-defined]
    from models.spec import SegmentSpec

    spec.episodes[0] = spec.episodes[0].model_copy(
        update={
            "status": "scripted",
            "segments": [
                SegmentSpec(index=1, narration="x", image_prompt="y", image_label="z")
            ],
        }
    )
    m1_client.store.enqueued = None  # type: ignore[attr-defined]
    resp = m1_client.post("/episodes/e1/script")
    assert resp.status_code == 200
    assert len(resp.json()["episode"]["segments"]) == 1
    assert m1_client.store.enqueued is None  # no enqueue  # type: ignore[attr-defined]


def test_episode_script_404(m1_client):
    assert m1_client.post("/episodes/missing/script").status_code == 404


# --------------------------------------------------------------------------- #
# GET /episodes/{id}  → spec + signed asset URLs                              #
# --------------------------------------------------------------------------- #
class _EpRow:
    def __init__(self, paths):
        self.paths = paths


def test_get_episode_returns_spec_and_no_assets_when_draft(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    resp = m1_client.get("/episodes/e1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["series_id"] == "s1"
    assert body["episode"]["episode_id"] == "e1"
    assert body["assets"]["videoUrl"] is None
    assert body["assets"]["thumbnails"] == []


def test_get_episode_returns_signed_assets_when_assembled(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    m1_client.store.episodes["e1"] = _EpRow(  # type: ignore[attr-defined]
        {
            "final": "projects/u/e1/final.mp4",
            "srt": "projects/u/e1/subs.srt",
            "thumbnails": "projects/u/e1/thumbnails/thumb_1.png,projects/u/e1/thumbnails/thumb_2.png",
        }
    )
    body = m1_client.get("/episodes/e1").json()
    assert body["assets"]["videoUrl"].endswith("final.mp4")
    assert body["assets"]["srtUrl"].endswith("subs.srt")
    assert len(body["assets"]["thumbnails"]) == 2


def test_get_episode_404(m1_client):
    assert m1_client.get("/episodes/missing").status_code == 404


# --------------------------------------------------------------------------- #
# /style/infer                                                                 #
# --------------------------------------------------------------------------- #
def _solid_png(rgb=(180, 100, 40)) -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    w = h = 4
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = (b"\x00" + bytes(rgb) * w) * h
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def test_style_infer_from_uploaded_png(m1_client):
    files = [("reference_images", ("ref.png", _solid_png(), "image/png"))]
    resp = m1_client.post("/style/infer", files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["palette"]
    assert body["description"]


def test_style_infer_no_images_returns_default(m1_client):
    resp = m1_client.post("/style/infer")
    assert resp.status_code == 200
    assert resp.json()["palette"]
