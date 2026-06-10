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
        # episode_id -> latest parent gen-job row (GET /episodes generation lookup)
        self.jobs: dict[str, object] = {}
        # episode_id -> list of child gen-job rows
        self.job_children: dict[str, list] = {}

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

        @staticmethod
        def script_started_at(paths):
            started = (paths or {}).get("script_started_at")
            return started if isinstance(started, str) else None

    class FakeGenJobRepo:
        def __init__(self, session):
            pass

        async def latest_parent_for_episode(self, user_id, episode_id):
            return store.jobs.get(episode_id)

        async def children_for_episode(self, user_id, episode_id):
            return getattr(store, "job_children", {}).get(episode_id, [])

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

    class FakeApiKeyRepo:
        def __init__(self, session):
            pass

        async def list_refs(self, user_id):
            return [_KeyRow(r) for r in getattr(store, "key_refs", set())]

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
    monkeypatch.setattr(episodes_router, "GenJobRepo", FakeGenJobRepo)
    monkeypatch.setattr(episodes_router, "get_storage", lambda: FakeStorage())
    # series router
    monkeypatch.setattr(series_router, "save_series_spec", fake_save)
    monkeypatch.setattr(series_router, "SeriesRepo", FakeSeriesRepo)
    monkeypatch.setattr(series_router, "spec_from_row", fake_spec_from_row)
    monkeypatch.setattr(series_router, "ApiKeyRepo", FakeApiKeyRepo)

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


class _KeyRow:
    def __init__(self, key_ref):
        self.key_ref = key_ref
        self.valid = True


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


def test_wizard_message_uses_requested_provider(m1_client, monkeypatch):
    """The per-series script provider from the request is passed to Phase A."""
    import web.routers.wizard as wizard_router

    captured: dict[str, str] = {}

    async def fake_phase_a(idea, history, *, ctx, provider="stub-script", **kw):
        captured["provider"] = provider
        from module1.wizard import PhaseAResult

        return PhaseAResult(reply="ok", outline=None)

    monkeypatch.setattr(wizard_router, "run_phase_a", fake_phase_a)
    resp = m1_client.post(
        "/wizard/message",
        json={"idea": "x", "history": [], "provider": "claude"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["provider"] == "claude"


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


def test_wizard_approve_uses_per_series_providers(m1_client):
    """Providers come from the request config (per-series toolset), set verbatim."""
    body = _config_body()
    body["providers"] = {"script": "claude", "image": "kie", "voice": "eleven"}
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


def test_wizard_approve_omnivoice_is_clone_no_sample(m1_client):
    """A per-series OmniVoice voice provider → series.voice is clone mode, no sample.

    The voice sample is uploaded per-series AFTER approve (POST
    /series/{id}/voice-sample), so approve only flips the config to clone mode.
    """
    body = _config_body()
    body["providers"] = {"script": "claude", "image": "kie", "voice": "omnivoice"}
    resp = m1_client.post(
        "/wizard/approve",
        json={
            "name": "Faiths",
            "topic": "religion",
            "outline": [{"id": "w1", "title": "Origins", "desc": "", "pick": True}],
            "config": body,  # request voice is edge/preset — overridden by chosen omnivoice
        },
    )
    assert resp.status_code == 200, resp.text
    voice = resp.json()["series"]["voice"]
    assert voice["provider"] == "omnivoice"
    assert voice["mode"] == "clone"
    assert voice["voice_id"] == ""
    assert voice["voice_sample"] is None


def test_wizard_approve_defaults_when_providers_omitted(m1_client):
    """Older clients that omit providers get keyless defaults (no hard-fail)."""
    body = _config_body()
    body.pop("providers", None)
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
    assert providers == {"script": "stub-script", "image": "web", "voice": "edge"}


def test_wizard_message_falls_back_when_no_provider_requested(m1_client, monkeypatch):
    """No per-series provider yet → falls back to a key-ready / stub script provider."""
    import web.routers.wizard as wizard_router

    captured: dict[str, str] = {}

    async def fake_phase_a(idea, history, *, ctx, provider="stub-script", **kw):
        captured["provider"] = provider
        from module1.wizard import PhaseAResult

        return PhaseAResult(reply="ok", outline=None)

    monkeypatch.setattr(wizard_router, "run_phase_a", fake_phase_a)
    resp = m1_client.post("/wizard/message", json={"idea": "x", "history": []})
    assert resp.status_code == 200, resp.text
    # No keys present in the fake → first keyless script provider (stub-script).
    assert captured["provider"] == "stub-script"


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
# GET /series/{id}/readiness  → per-series toolset + per-user key gate         #
# --------------------------------------------------------------------------- #
def test_series_readiness_not_ready_without_key(m1_client):
    # Seed a series whose image provider (kie) needs a key the user lacks.
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    m1_client.store.key_refs = set()  # type: ignore[attr-defined]
    body = m1_client.get("/series/s1/readiness").json()
    assert body["series_id"] == "s1"
    # stub-script is keyless → script ready; kie needs a key → image not ready.
    assert body["script_ready"] is True
    assert body["image_ready"] is False
    assert body["ready"] is False
    assert any("kie" in m for m in body["missing"])


def test_series_readiness_ready_with_key(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    m1_client.store.key_refs = {"kie"}  # type: ignore[attr-defined]
    body = m1_client.get("/series/s1/readiness").json()
    assert body["script_ready"] is True
    assert body["image_ready"] is True
    assert body["voice_ready"] is True  # edge is keyless
    assert body["ready"] is True
    assert body["missing"] == []


def test_series_readiness_omnivoice_needs_sample(m1_client):
    from models.spec import VoiceConfig

    spec = _seed_series(m1_client.store)  # type: ignore[attr-defined]
    spec.providers = {**spec.providers, "image": "web", "voice": "omnivoice"}
    spec.voice = VoiceConfig(provider="omnivoice", voice_id="", mode="clone")
    m1_client.store.key_refs = set()  # type: ignore[attr-defined]
    body = m1_client.get("/series/s1/readiness").json()
    assert body["voice_ready"] is False
    assert any("giọng mẫu" in m for m in body["missing"])


def test_series_readiness_404(m1_client):
    assert m1_client.get("/series/nope/readiness").status_code == 404


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


class _JobRow:
    """Minimal parent/child gen-job stand-in for the generation-lookup tests."""

    def __init__(self, **kw):
        self.id = kw.get("id", "parent_x")
        self.name = kw.get("name", "ep")
        self.icon = kw.get("icon", "")
        self.state = kw.get("state", "queued")
        self.progress = kw.get("progress", 0)
        self.stderr = kw.get("stderr")
        self.created_at = kw.get("created_at")


def test_get_episode_surfaces_script_started_at_when_running(m1_client):
    _seed_series(m1_client.store)  # type: ignore[attr-defined]
    m1_client.store.episodes["e1"] = _EpRow(  # type: ignore[attr-defined]
        {"script_status": "running", "script_started_at": "2026-06-10T00:00:00+00:00"}
    )
    body = m1_client.get("/episodes/e1").json()
    assert body["script_status"] == "running"
    assert body["script_started_at"] == "2026-06-10T00:00:00+00:00"
    assert body["generation"] is None


def test_get_episode_returns_generation_lookup_when_producing(m1_client):
    from datetime import datetime, timezone

    store = m1_client.store  # type: ignore[attr-defined]
    _seed_series(store)
    ts = datetime(2026, 6, 10, 1, 2, 3, tzinfo=timezone.utc)
    store.jobs["e1"] = _JobRow(id="parent_1", state="running", created_at=ts)
    store.job_children["e1"] = [
        _JobRow(id="c1", name="Voiceover", icon="mic", state="done", progress=100),
        _JobRow(id="c2", name="Image 1", icon="image", state="running", progress=10),
    ]
    body = m1_client.get("/episodes/e1").json()
    gen = body["generation"]
    assert gen is not None
    assert gen["jobId"] == "parent_1"
    assert gen["state"] == "running"  # a child still running
    assert gen["started_at"] == ts.isoformat()
    assert {j["id"] for j in gen["jobs"]} == {"c1", "c2"}


def test_get_episode_generation_done_when_all_children_done(m1_client):
    store = m1_client.store  # type: ignore[attr-defined]
    _seed_series(store)
    store.jobs["e1"] = _JobRow(id="parent_1", state="done")
    store.job_children["e1"] = [
        _JobRow(id="c1", state="done", progress=100),
        _JobRow(id="c2", state="done", progress=100),
    ]
    gen = m1_client.get("/episodes/e1").json()["generation"]
    assert gen["state"] == "done"
    assert gen["started_at"] is None  # parent had no created_at


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
