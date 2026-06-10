"""Resume-unfinished produce (non-destructive recovery after a frozen run).

Two layers:

1. ``seed_children`` is idempotent — re-enqueuing ``produce_episode`` (what the
   resume endpoint does) must REUSE the existing child set, not insert a duplicate
   one (otherwise the UI shows doubled "Voiceover" / "Image N" rows).
2. ``POST /episodes/{id}/resume-production`` re-queues every non-``done`` child,
   keeps the ``done`` ones, re-enqueues produce, and surfaces the refreshed
   lookup. DB + enqueue are faked (no live Postgres/Redis).
"""

from __future__ import annotations

import pytest

import web.routers.episodes as episodes_router
from models.spec import EpisodeSpec, ImageStyle, SegmentSpec, SeriesSpec, VoiceConfig
from module2 import jobs as jobmod
from web.deps import get_current_user, get_db

FAKE_USER_ID = "u_test"


# --------------------------------------------------------------------------- #
# 1. seed_children idempotency (unit)                                         #
# --------------------------------------------------------------------------- #
class _FakeJobRepo:
    """In-memory GenJobRepo over a dict, enough for seed_parent/seed_children."""

    def __init__(self) -> None:
        self.store: dict = {}
        self.s = self

    async def flush(self):
        return None

    async def add(self, row):
        self.store[row.id] = row
        return row

    async def children_for_episode(self, user_id, episode_id):
        return [
            r
            for r in self.store.values()
            if r.episode_id == episode_id and r.user_id == user_id and r.parent_id is not None
        ]


def _ep(n: int = 3) -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="e1", title="T", order=1, status="scripted",
        segments=[
            SegmentSpec(index=i, narration="n", image_prompt="p", image_label=f"l{i}")
            for i in range(1, n + 1)
        ],
    )


async def test_seed_children_idempotent_reuses_existing():
    """A second seed for the same parent reuses the rows (no duplicate set)."""
    repo = _FakeJobRepo()
    ep = _ep(3)
    parent_id = await jobmod.seed_parent(repo, "u", ep)
    first = await jobmod.seed_children(repo, "u", ep, parent_id)
    n_rows = len(repo.store)  # parent + voice + 3 images + render + thumbnail = 7

    second = await jobmod.seed_children(repo, "u", ep, parent_id)

    assert len(repo.store) == n_rows  # nothing new inserted
    assert second.voice_id == first.voice_id
    assert second.render_id == first.render_id
    assert second.thumbnail_id == first.thumbnail_id
    # Image ids come back in segment order both times (run_images zips with segments).
    assert second.image_ids == first.image_ids
    assert len(second.image_ids) == 3


async def test_seed_children_fresh_when_segment_count_differs():
    """A changed segment count cannot reuse the old set (zip would misalign)."""
    repo = _FakeJobRepo()
    parent_id = await jobmod.seed_parent(repo, "u", _ep(3))
    await jobmod.seed_children(repo, "u", _ep(3), parent_id)
    # Now the script has 2 segments — the 3-image set must NOT be reused.
    reseeded = await jobmod.seed_children(repo, "u", _ep(2), parent_id)
    assert len(reseeded.image_ids) == 2


# --------------------------------------------------------------------------- #
# 2. POST /episodes/{id}/resume-production (endpoint)                          #
# --------------------------------------------------------------------------- #
def _spec() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="Faiths", topic="t", skill="religion",
        language="vi", target_minutes=5, density="standard",
        providers={"script": "stub-script", "image": "kie", "voice": "edge"},
        image_style=ImageStyle(preset_id="p", base_prompt="base", aspect="16:9"),
        voice=VoiceConfig(provider="edge", voice_id="v"),
        episodes=[
            EpisodeSpec(
                episode_id="e1", title="Origins", order=1, status="assets",
                segments=[
                    SegmentSpec(index=1, narration="n1", image_prompt="p1", image_label="a"),
                    SegmentSpec(index=2, narration="n2", image_prompt="p2", image_label="b"),
                ],
            ),
            EpisodeSpec(episode_id="e2", title="Second", order=2, status="draft"),
        ],
    )


class _Child:
    def __init__(self, cid, kind, name, state, progress=0):
        self.id = cid
        self.parent_id = "parent_1"
        self.kind = kind
        self.name = name
        self.icon = ""
        self.state = state
        self.progress = progress
        self.stderr = "boom" if state == "error" else None


class _Parent:
    def __init__(self):
        self.id = "parent_1"
        self.episode_id = "e1"
        self.parent_id = None
        self.state = "running"
        self.stderr = None
        self.created_at = None


@pytest.fixture()
def resume_client(monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    parent = _Parent()
    # done ones (kept) + a stuck running + a queued + a failed (all → re-queued).
    children = [
        _Child("voice_1", "voice", "Voiceover", "done", 100),
        _Child("image_1", "image", "Image 1: a", "done", 100),
        _Child("image_2", "image", "Image 2: b", "running", 30),
        _Child("render_1", "render", "Render video", "queued", 0),
        _Child("thumb_1", "thumbnail", "Thumbnails", "error", 0),
    ]
    enq: list = []

    async def fake_find(session, user_id, episode_id):
        sp = _spec()
        ep = next((e for e in sp.episodes if e.episode_id == episode_id), None)
        return ("row", sp, ep) if ep is not None else None

    class FakeGenJobRepo:
        def __init__(self, session):
            pass

        async def latest_parent_for_episode(self, user_id, episode_id):
            return parent if episode_id == "e1" else None

        async def children_for_episode(self, user_id, episode_id):
            return list(children) if episode_id == "e1" else []

    class FakeEpisodeRepo:
        def __init__(self, session):
            pass

        async def set_status(self, user_id, episode_id, status):
            return None

    async def fake_enqueue(fn, *args, **kw):
        enq.append((fn, args))
        return "job_x"

    class FakeSession:
        async def flush(self):
            return None

    monkeypatch.setattr(episodes_router, "find_series_for_episode", fake_find)
    monkeypatch.setattr(episodes_router, "GenJobRepo", FakeGenJobRepo)
    monkeypatch.setattr(episodes_router, "EpisodeRepo", FakeEpisodeRepo)
    monkeypatch.setattr(episodes_router, "enqueue_job", fake_enqueue)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID

    async def _fake_db():
        yield FakeSession()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.children = children  # type: ignore[attr-defined]
    client.enq = enq  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def test_resume_requeues_unfinished_keeps_done(resume_client):
    resp = resume_client.post("/episodes/e1/resume-production")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 3 non-done children (running + queued + error) re-queued; 2 done kept.
    assert body["requeued"] == 3
    by_id = {c.id: c for c in resume_client.children}
    assert by_id["voice_1"].state == "done"
    assert by_id["image_1"].state == "done"
    assert by_id["image_2"].state == "queued" and by_id["image_2"].progress == 0
    assert by_id["render_1"].state == "queued"
    assert by_id["thumb_1"].state == "queued" and by_id["thumb_1"].stderr is None

    # Produce re-enqueued exactly once for this episode.
    assert resume_client.enq == [("produce_episode", (FAKE_USER_ID, "e1"))]

    # Lookup surfaces the parent job + child list so the UI resumes polling.
    gen = body["generation"]
    assert gen["jobId"] == "parent_1"
    assert gen["state"] == "running"
    assert len(gen["jobs"]) == 5


def test_resume_never_produced_409(resume_client):
    resp = resume_client.post("/episodes/e2/resume-production")
    assert resp.status_code == 409
    assert resume_client.enq == []  # nothing enqueued


def test_resume_missing_episode_404(resume_client):
    resp = resume_client.post("/episodes/nope/resume-production")
    assert resp.status_code == 404
