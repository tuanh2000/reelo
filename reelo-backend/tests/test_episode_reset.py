"""POST /episodes/{id}/reset (B) — destructive reset to outline-only draft.

Verifies the endpoint clears the script (segments) + youtube in spec_json, resets
status to draft, clears script status / curation / paths (incl. the resume asset
manifest), deletes the episode's gen_jobs, and wipes the storage prefix. DB +
storage are faked (no live Postgres/S3).
"""

from __future__ import annotations

import pytest

import web.routers.episodes as episodes_router
from models.spec import EpisodeSpec, ImageStyle, SegmentSpec, SeriesSpec, VoiceConfig
from module2 import resume as resume_mod
from web.deps import get_current_user, get_db

FAKE_USER_ID = "u_test"


def _spec() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="Faiths", topic="religion", skill="religion",
        language="vi", target_minutes=5, density="standard",
        providers={"script": "stub-script", "image": "kie", "voice": "edge"},
        image_style=ImageStyle(preset_id="p", base_prompt="base", aspect="16:9"),
        voice=VoiceConfig(provider="edge", voice_id="v"),
        episodes=[
            EpisodeSpec(
                episode_id="e1", title="Origins", order=1, desc="d", target_minutes=7,
                status="assembled",
                youtube={"title": "Y", "description": "d", "tags": ["a"]},
                segments=[
                    SegmentSpec(index=1, narration="n1", image_prompt="p1", image_label="a"),
                    SegmentSpec(index=2, narration="n2", image_prompt="p2", image_label="b"),
                ],
            ),
            EpisodeSpec(episode_id="e2", title="Second", order=2, status="draft"),
        ],
    )


class _Row:
    def __init__(self, spec):
        self.spec_json = spec.model_dump()
        self.id = spec.series_id


class _EpRow:
    def __init__(self, ep_id, status="assembled"):
        self.id = ep_id
        self.status = status
        self.paths = {
            "final": "projects/u_test/e1/final.mp4",
            "thumbnails": "projects/u_test/e1/thumbnails/thumb_1.png",
            "script_status": "done",
            resume_mod.MANIFEST_KEY: {"images": {"1": "h1", "2": "h2"}, "voice": "hv"},
        }
        self.urls = {"final": "https://signed/x"}
        self.image_curation = {"provider": "web", "segments": []}


@pytest.fixture()
def reset_client(monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    spec = _spec()
    series_row = _Row(spec)
    ep_row = _EpRow("e1")
    state = {
        "deleted_jobs": 0,
        "deleted_prefix": None,
        "spec_json": series_row.spec_json,
    }

    async def fake_find_for_episode(session, user_id, episode_id):
        from models.spec import SeriesSpec as _SS

        sp = _SS.model_validate(state["spec_json"])
        ep = next((e for e in sp.episodes if e.episode_id == episode_id), None)
        if ep is None:
            return None
        return series_row, sp, ep

    async def fake_reset_outline(session, user_id, series_id, episode_id):
        from module1.persistence import (
            find_episode_in_spec,
            spec_from_row,
        )

        # Mutate the captured spec_json the way the real helper would.
        series_row.spec_json = state["spec_json"]
        sp = spec_from_row(series_row)
        target = find_episode_in_spec(sp, episode_id)
        if target is None:
            return None
        reset = EpisodeSpec(
            episode_id=target.episode_id, title=target.title, order=target.order,
            desc=target.desc, target_minutes=target.target_minutes,
            status="draft", youtube=None, segments=[],
        )
        sp.episodes = [reset if e.episode_id == episode_id else e for e in sp.episodes]
        state["spec_json"] = sp.model_dump()
        ep_row.status = "draft"
        return reset

    class FakeGenJobRepo:
        def __init__(self, session):
            pass

        async def delete_for_episode(self, user_id, episode_id):
            state["deleted_jobs"] = 3  # parent + 2 children
            return 3

    class FakeEpisodeRepo:
        def __init__(self, session):
            pass

        async def reset_to_draft(self, user_id, episode_id):
            ep_row.status = "draft"
            ep_row.paths = {}
            ep_row.urls = {}
            ep_row.image_curation = None
            return ep_row

    class FakeStorage:
        async def delete_prefix(self, prefix):
            state["deleted_prefix"] = prefix
            return 5  # images/voice/final/thumbs

    monkeypatch.setattr(episodes_router, "find_series_for_episode", fake_find_for_episode)
    monkeypatch.setattr(episodes_router, "reset_episode_to_outline", fake_reset_outline)
    monkeypatch.setattr(episodes_router, "GenJobRepo", FakeGenJobRepo)
    monkeypatch.setattr(episodes_router, "EpisodeRepo", FakeEpisodeRepo)
    monkeypatch.setattr(episodes_router, "get_storage", lambda: FakeStorage())

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.state = state  # type: ignore[attr-defined]
    client.ep_row = ep_row  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def test_reset_clears_spec_status_curation_jobs_storage(reset_client):
    resp = reset_client.post("/episodes/e1/reset")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # 1. spec → outline-only draft (no segments, no youtube, title kept).
    ep = body["episode"]
    assert ep["episode_id"] == "e1"
    assert ep["status"] == "draft"
    assert ep["segments"] == []
    assert ep["youtube"] is None
    assert ep["title"] == "Origins"  # outline identity preserved
    assert ep["target_minutes"] == 7

    # 3. jobs deleted; 4. storage prefix wiped.
    assert body["jobs_deleted"] == 3
    assert body["assets_deleted"] == 5
    assert reset_client.state["deleted_prefix"] == "projects/u_test/e1"

    # 2. row-level paths/urls/curation cleared (incl. resume asset manifest).
    assert reset_client.ep_row.paths == {}
    assert reset_client.ep_row.urls == {}
    assert reset_client.ep_row.image_curation is None
    assert reset_client.ep_row.status == "draft"

    # The persisted spec_json no longer carries e1's segments.
    sp = reset_client.state["spec_json"]
    e1 = next(e for e in sp["episodes"] if e["episode_id"] == "e1")
    assert e1["segments"] == []
    # Sibling episode untouched.
    e2 = next(e for e in sp["episodes"] if e["episode_id"] == "e2")
    assert e2["title"] == "Second"


def test_reset_missing_episode_404(reset_client):
    resp = reset_client.post("/episodes/nope/reset")
    assert resp.status_code == 404


def test_reset_storage_failure_is_non_fatal(reset_client, monkeypatch):
    """A storage error during prefix-delete must not fail the reset (DB is truth)."""

    class BoomStorage:
        async def delete_prefix(self, prefix):
            raise RuntimeError("s3 down")

    monkeypatch.setattr(episodes_router, "get_storage", lambda: BoomStorage())
    resp = reset_client.post("/episodes/e1/reset")
    assert resp.status_code == 200, resp.text
    assert resp.json()["assets_deleted"] == 0
