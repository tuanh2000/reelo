"""Module 2 web endpoints: /generation/{start,poll,retry}, /series/{id}/music,
/publish/export.

DB session + repos + enqueue + storage are faked (no live Postgres/Redis/S3).
"""

from __future__ import annotations


import pytest

import web.routers.generation as gen_router
import web.routers.publish as pub_router
import web.routers.series as series_router
from models.spec import (
    EpisodeSpec,
    ImageStyle,
    SegmentSpec,
    SeriesSpec,
    VoiceConfig,
)
from web.deps import get_current_user, get_db

FAKE_USER_ID = "u_test"


# --------------------------------------------------------------------------- #
# In-memory state                                                             #
# --------------------------------------------------------------------------- #
class JobRow:
    def __init__(self, **kw):
        self.id = kw["id"]
        self.user_id = kw["user_id"]
        self.episode_id = kw["episode_id"]
        self.parent_id = kw.get("parent_id")
        self.kind = kw["kind"]
        self.name = kw["name"]
        self.icon = kw.get("icon", "")
        self.state = kw.get("state", "queued")
        self.progress = kw.get("progress", 0)
        self.stderr = kw.get("stderr")
        self.created_at = kw.get("created_at")


class EpRow:
    def __init__(self, ep_id):
        self.id = ep_id
        self.status = "assembled"
        self.paths = {}


class Store:
    def __init__(self):
        self.jobs: dict[str, JobRow] = {}
        self.series: dict[str, SeriesSpec] = {}
        self.episodes: dict[str, EpRow] = {}
        self.enqueued: list[tuple] = []
        self.storage_puts: dict[str, bytes] = {}


@pytest.fixture()
def store():
    return Store()


def _spec(series_id="s1", ep=None) -> SeriesSpec:
    return SeriesSpec(
        series_id=series_id, name="Faiths", topic="religion", skill="religion",
        language="vi", target_minutes=5, density="standard",
        providers={"script": "stub-script", "image": "stub-image", "voice": "stub-voice"},
        image_style=ImageStyle(preset_id="p", base_prompt="b", aspect="16:9"),
        voice=VoiceConfig(provider="stub-voice", voice_id="v"),
        episodes=[ep] if ep else [],
    )


def _scripted_ep(ep_id="e1") -> EpisodeSpec:
    return EpisodeSpec(
        episode_id=ep_id, title="Origins", order=1, status="scripted",
        youtube={"title": "YT Title", "description": "d", "tags": ["a"]},
        segments=[
            SegmentSpec(index=i, narration="word " * 10, image_prompt="x", image_label=f"l{i}")
            for i in range(1, 3)
        ],
    )


class _FakeGenJobRepo:
    def __init__(self, store):
        self.store = store
        self.s = self

    async def flush(self):
        return None

    async def add(self, row):
        self.store.jobs[row.id] = row
        return row

    async def get(self, user_id, job_id):
        r = self.store.jobs.get(job_id)
        return r if r and r.user_id == user_id else None

    async def children_for_episode(self, user_id, episode_id):
        return [
            r for r in self.store.jobs.values()
            if r.episode_id == episode_id and r.user_id == user_id and r.parent_id
        ]


class _FakeEpisodeRepo:
    def __init__(self, store):
        self.store = store

    async def get(self, user_id, episode_id):
        return self.store.episodes.get(episode_id)


class _FakeSeriesRepo:
    def __init__(self, store):
        self.store = store

    async def get(self, user_id, series_id):
        sp = self.store.series.get(series_id)
        return _SeriesRow(sp) if sp else None


class _SeriesRow:
    def __init__(self, spec):
        self.spec = spec
        self.spec_json = spec.model_dump() if spec else {}
        self.id = spec.series_id if spec else None


class _FakeStorage:
    def __init__(self, store):
        self.store = store

    async def put(self, key, data, **kw):
        self.store.storage_puts[key] = data
        return key

    async def signed_url(self, key, **kw):
        return f"https://signed/{key}"


@pytest.fixture()
def m2_client(store, monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    async def fake_enqueue(function, *args, **kwargs):
        store.enqueued.append((function, args))
        return "job-xyz"

    def fake_find(spec_store):
        async def _find(session, user_id, episode_id):
            for sid, sp in spec_store.items():
                for e in sp.episodes:
                    if e.episode_id == episode_id:
                        return _SeriesRow(sp), sp, e
            return None
        return _find

    # generation router
    monkeypatch.setattr(gen_router, "GenJobRepo", lambda s: _FakeGenJobRepo(store))
    monkeypatch.setattr(gen_router, "enqueue_job", fake_enqueue)
    monkeypatch.setattr(gen_router, "find_series_for_episode", fake_find(store.series))

    class _FakeApiKeyRepo:
        def __init__(self, s):
            pass

        async def list_refs(self, user_id):
            return []  # no keys needed: the seeded specs use keyless stub providers

    monkeypatch.setattr(gen_router, "ApiKeyRepo", _FakeApiKeyRepo)
    # publish router
    monkeypatch.setattr(pub_router, "EpisodeRepo", lambda s: _FakeEpisodeRepo(store))
    monkeypatch.setattr(pub_router, "get_storage", lambda: _FakeStorage(store))
    monkeypatch.setattr(pub_router, "find_series_for_episode", fake_find(store.series))
    # series router (music)
    monkeypatch.setattr(series_router, "SeriesRepo", lambda s: _FakeSeriesRepo(store))
    monkeypatch.setattr(series_router, "get_storage", lambda: _FakeStorage(store))

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID

    class _FakeDb:
        async def flush(self):
            return None

    async def _fake_db():
        yield _FakeDb()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.store = store  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# /generation/start                                                          #
# --------------------------------------------------------------------------- #
def test_start_generation_seeds_parent_and_enqueues(m2_client):
    store = m2_client.store
    ep = _scripted_ep()
    store.series["s1"] = _spec(ep=ep)

    resp = m2_client.post("/generation/start", json={"series_id": "s1", "episode_id": "e1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["jobId"].startswith("parent_")
    assert body["cost_estimate"]["images"] == 2
    # parent job seeded, produce enqueued
    parents = [j for j in store.jobs.values() if j.parent_id is None]
    assert len(parents) == 1
    assert ("produce_episode", (FAKE_USER_ID, "e1")) in store.enqueued
    # scripted -> no generate_script enqueue
    assert all(f != "generate_script" for f, _ in store.enqueued)


def test_start_generation_unscripted_enqueues_produce_only(m2_client):
    """An unscripted produce enqueues ONLY produce_episode — NOT generate_script.

    Scripting is owned solely by produce's ensure_scripted (step 0). Enqueuing a
    separate generate_script would race it: two non-deterministic LLM scripts, one
    saved to spec_json while images are built from the other → every asset later
    looks "changed" and regenerates (orphaned images). Regression guard for that.
    """
    store = m2_client.store
    ep = EpisodeSpec(episode_id="e2", title="Draft", order=2)  # no segments
    store.series["s1"] = _spec(ep=ep)

    resp = m2_client.post("/generation/start", json={"series_id": "s1", "episode_id": "e2"})
    assert resp.status_code == 200, resp.text
    funcs = [f for f, _ in store.enqueued]
    assert "produce_episode" in funcs
    assert "generate_script" not in funcs  # single script-gen owner (no race)
    # cost estimate derived from target/density even when unscripted
    assert resp.json()["cost_estimate"]["images"] > 0


def test_start_generation_404_when_missing(m2_client):
    resp = m2_client.post("/generation/start", json={"series_id": "s1", "episode_id": "nope"})
    assert resp.status_code == 404


def test_start_generation_409_when_series_not_ready(m2_client):
    """A series whose image provider needs a key the user lacks → 409 (gate)."""
    store = m2_client.store
    ep = _scripted_ep()
    spec = _spec(ep=ep)
    spec.providers = {**spec.providers, "image": "kie"}  # kie needs a key
    store.series["s1"] = spec

    resp = m2_client.post("/generation/start", json={"series_id": "s1", "episode_id": "e1"})
    assert resp.status_code == 409, resp.text
    assert "kie" in resp.json()["detail"]


def test_start_generation_requires_auth():
    from fastapi.testclient import TestClient

    from web.app import create_app

    anon = TestClient(create_app())
    r = anon.post("/generation/start", json={"series_id": "s1", "episode_id": "e1"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# /generation/{job_id} (poll)                                                #
# --------------------------------------------------------------------------- #
def test_poll_returns_children(m2_client):
    store = m2_client.store
    parent = JobRow(id="parent_1", user_id=FAKE_USER_ID, episode_id="e1", kind="parent", name="ep")
    store.jobs["parent_1"] = parent
    store.jobs["c1"] = JobRow(
        id="c1", user_id=FAKE_USER_ID, episode_id="e1", parent_id="parent_1",
        kind="voice", name="Voiceover", icon="mic", state="done", progress=100,
    )
    store.jobs["c2"] = JobRow(
        id="c2", user_id=FAKE_USER_ID, episode_id="e1", parent_id="parent_1",
        kind="image", name="Image 1", icon="image", state="running", progress=10,
    )
    resp = m2_client.get("/generation/parent_1")
    assert resp.status_code == 200, resp.text
    jobs = resp.json()["jobs"]
    assert len(jobs) == 2
    by_id = {j["id"]: j for j in jobs}
    assert by_id["c1"]["state"] == "done" and by_id["c1"]["icon"] == "mic"
    assert by_id["c2"]["state"] == "running"


def test_poll_returns_parent_started_at(m2_client):
    from datetime import datetime, timezone

    store = m2_client.store
    ts = datetime(2026, 6, 10, 3, 4, 5, tzinfo=timezone.utc)
    store.jobs["parent_1"] = JobRow(
        id="parent_1", user_id=FAKE_USER_ID, episode_id="e1", kind="parent",
        name="ep", created_at=ts,
    )
    store.jobs["c1"] = JobRow(
        id="c1", user_id=FAKE_USER_ID, episode_id="e1", parent_id="parent_1",
        kind="voice", name="Voiceover", state="running", progress=10,
    )
    body = m2_client.get("/generation/parent_1").json()
    assert body["started_at"] == ts.isoformat()
    assert len(body["jobs"]) == 1


def test_poll_404_missing_job(m2_client):
    assert m2_client.get("/generation/nope").status_code == 404


# --------------------------------------------------------------------------- #
# /generation/{job_id}/retry/{child_id}                                      #
# --------------------------------------------------------------------------- #
def test_retry_child_requeues_and_reenqueues(m2_client):
    store = m2_client.store
    store.jobs["parent_1"] = JobRow(
        id="parent_1", user_id=FAKE_USER_ID, episode_id="e1", kind="parent", name="ep"
    )
    store.jobs["c1"] = JobRow(
        id="c1", user_id=FAKE_USER_ID, episode_id="e1", parent_id="parent_1",
        kind="image", name="Image 1", state="error", progress=0, stderr="boom",
    )
    resp = m2_client.post("/generation/parent_1/retry/c1")
    assert resp.status_code == 200, resp.text
    assert store.jobs["c1"].state == "queued"
    assert store.jobs["c1"].stderr is None
    assert ("produce_episode", (FAKE_USER_ID, "e1")) in store.enqueued


def test_retry_child_404_wrong_parent(m2_client):
    store = m2_client.store
    store.jobs["parent_1"] = JobRow(
        id="parent_1", user_id=FAKE_USER_ID, episode_id="e1", kind="parent", name="ep"
    )
    store.jobs["other"] = JobRow(
        id="other", user_id=FAKE_USER_ID, episode_id="e1", parent_id="parent_999",
        kind="image", name="x",
    )
    assert m2_client.post("/generation/parent_1/retry/other").status_code == 404


# --------------------------------------------------------------------------- #
# /series/{id}/music                                                         #
# --------------------------------------------------------------------------- #
def test_upload_music_stores_and_records_path(m2_client):
    store = m2_client.store
    store.series["s1"] = _spec()
    files = {"file": ("bg.mp3", b"ID3audiodata", "audio/mpeg")}
    resp = m2_client.post("/series/s1/music", files=files)
    assert resp.status_code == 200, resp.text
    key = resp.json()["path"]
    assert "music/bg.mp3" in key
    assert store.storage_puts[key] == b"ID3audiodata"


def test_upload_music_404_when_series_missing(m2_client):
    files = {"file": ("bg.mp3", b"x", "audio/mpeg")}
    assert m2_client.post("/series/nope/music", files=files).status_code == 404


def test_upload_music_400_empty(m2_client):
    m2_client.store.series["s1"] = _spec()
    files = {"file": ("bg.mp3", b"", "audio/mpeg")}
    assert m2_client.post("/series/s1/music", files=files).status_code == 400


# --------------------------------------------------------------------------- #
# /publish/export                                                            #
# --------------------------------------------------------------------------- #
def test_publish_export_returns_signed_urls(m2_client):
    store = m2_client.store
    ep = _scripted_ep()
    store.series["s1"] = _spec(ep=ep)
    ep_row = EpRow("e1")
    ep_row.paths = {
        "final": "projects/u/e1/final.mp4",
        "srt": "projects/u/e1/subs.srt",
        "thumbnails": "projects/u/e1/thumbnails/thumb_1.png,projects/u/e1/thumbnails/thumb_2.png",
    }
    store.episodes["e1"] = ep_row

    body = {
        "series_id": "s1",
        "episode_id": "e1",
        "meta": {
            "title": "My Final Title",
            "description": "desc",
            "tags": ["x", "y"],
            "thumbnailIndex": 1,
        },
    }
    resp = m2_client.post("/publish/export", json=body)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["videoUrl"].endswith("final.mp4")
    assert out["srtUrl"].endswith("subs.srt")
    assert out["thumbnailUrl"].endswith("thumb_2.png")  # index 1
    # metadata merges youtube + review meta (review wins)
    assert out["metadata"]["title"] == "My Final Title"
    assert out["metadata"]["tags"] == ["x", "y"]


def test_publish_export_409_when_not_assembled(m2_client):
    store = m2_client.store
    store.series["s1"] = _spec(ep=_scripted_ep())
    ep_row = EpRow("e1")
    ep_row.paths = {}  # no final
    store.episodes["e1"] = ep_row
    body = {"series_id": "s1", "episode_id": "e1", "meta": {"title": "t", "description": "d"}}
    assert m2_client.post("/publish/export", json=body).status_code == 409


def test_publish_export_404_missing_episode(m2_client):
    body = {"series_id": "s1", "episode_id": "nope", "meta": {"title": "t", "description": "d"}}
    assert m2_client.post("/publish/export", json=body).status_code == 404
