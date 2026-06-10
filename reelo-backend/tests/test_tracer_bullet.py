"""End-to-end tracer-bullet (integration.md §8, milestone #1).

Proves the full Reelo pipeline runs **without any real API key and without a
real Google login**, using the keyless stub providers
(``stub-script`` / ``stub-voice`` / ``stub-image``) selected explicitly and the
auth dependency overridden to a fake user:

    login (fake user_id)
      → build a SeriesSpec via Module 1 Phase B (build_series_spec, providers=stub-*)
      → lazy script via Module 1 (generate_episode_script, real registry + stub-script)
      → produce via Module 2 (run_produce_episode, REAL ffmpeg render)
      → final.mp4 + subs.srt + 3 thumbnails on a REAL local-storage adapter
      → signed_url (what POST /publish/export returns)

The render is REAL: ffmpeg assembles an h264+aac mp4 from the stub PNGs and the
stub silent MP3, and the test asserts ffprobe can read it back and that the
episode status is ``assembled``.

Two layers:

1. ``test_tracer_bullet_pipeline_assembles_real_video`` — drives the runner
   directly with a small in-memory fake DB (no Postgres/Redis needed) + a real
   :class:`LocalObjectStorage`. This is the load-bearing milestone.
2. ``test_tracer_bullet_http_endpoints_wired`` — a TestClient e2e with the auth
   override + worker task functions called inline (no Redis), proving the web
   endpoints / worker entrypoints are wired together. DB-backed persistence is
   not required: the wizard message path degrades to an empty in-memory KeyStore
   and resolves the keyless stub-script provider.

How to run::

    cd reelo-backend
    .venv/bin/python -m pytest tests/test_tracer_bullet.py -v

The video-producing test self-skips if ffmpeg/ffprobe are not on PATH.
"""

from __future__ import annotations

import contextlib

import pytest

from clients.base import CallContext
from clients.registry import get_registry
from keystore import Cipher, KeyStore
from models.spec import ImageStyle, VoiceConfig
from module1.episode_script import generate_episode_script
from module1.wizard import build_series_spec
from module2 import ffmpeg
from storage.local import LocalObjectStorage
from usage import UsageLogger

FFMPEG = ffmpeg.ffmpeg_available()

STUB_PROVIDERS = {"script": "stub-script", "image": "stub-image", "voice": "stub-voice"}
FAKE_USER = "u_tracer"


# --------------------------------------------------------------------------- #
# In-memory fakes (no Postgres / Redis)                                       #
# --------------------------------------------------------------------------- #
class _EpRow:
    def __init__(self, ep_id: str) -> None:
        self.id = ep_id
        self.user_id = FAKE_USER
        self.status = "scripted"
        self.paths: dict = {}
        self.urls: dict = {}


class _FakeJobRepo:
    """In-memory GenJobRepo over a shared dict of rows (mirrors the ORM repo)."""

    def __init__(self, store: dict) -> None:
        self.store = store
        self.s = self  # the runner calls repo.s.flush()

    async def flush(self):
        return None

    async def add(self, row):
        self.store[row.id] = row
        return row

    async def get(self, user_id, job_id):
        row = self.store.get(job_id)
        return row if row and row.user_id == user_id else None

    async def children_for_episode(self, user_id, episode_id):
        return [
            r
            for r in self.store.values()
            if r.episode_id == episode_id and r.user_id == user_id and r.parent_id is not None
        ]


class _FakeEpisodeRepo:
    def __init__(self, ep_row: _EpRow) -> None:
        self.ep_row = ep_row

    async def get(self, user_id, episode_id):
        return self.ep_row if self.ep_row.id == episode_id else None

    async def set_paths(self, user_id, episode_id, paths, *, urls=None, status=None, merge=True):
        row = await self.get(user_id, episode_id)
        if row is None:
            return None
        row.paths = {**(row.paths or {}), **paths} if merge else dict(paths)
        if urls is not None:
            row.urls = {**(row.urls or {}), **urls} if merge else dict(urls)
        if status is not None:
            row.status = status
        return row


def _wire_runner(monkeypatch, *, spec, scripted, ep_row, jobs_store, storage):
    """Patch the runner's DB/session/storage seams to the in-memory fakes."""
    import module2.runner as runner

    job_repo = _FakeJobRepo(jobs_store)
    ep_repo = _FakeEpisodeRepo(ep_row)

    class _FakeSession:
        async def flush(self):
            return None

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield _FakeSession()

    monkeypatch.setattr(runner, "session_scope", fake_scope)
    monkeypatch.setattr(runner, "GenJobRepo", lambda session: job_repo)
    monkeypatch.setattr(runner, "EpisodeRepo", lambda session: ep_repo)
    monkeypatch.setattr(runner, "get_storage", lambda: storage)

    async def fake_ensure(user_id, episode_id, ctx):
        # Episode already lazily-scripted above; skip the DB round-trip.
        return spec, scripted

    monkeypatch.setattr(runner, "ensure_scripted", fake_ensure)

    async def fake_find_parent(repo, user_id, episode_id):
        return None  # runner seeds its own parent in direct-call mode

    monkeypatch.setattr(runner.jobmod, "find_parent_for_episode", fake_find_parent)
    return runner


def _ctx() -> CallContext:
    return CallContext(user_id=FAKE_USER, keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())


def _build_spec():
    """Module 1 Phase B: a SeriesSpec shell from an approved outline (stub-*)."""
    outline = [{"id": "w1", "title": "Origins", "desc": "how it began", "pick": True}]
    spec = build_series_spec(
        name="Faiths of the World",
        topic="comparative religion",
        outline=outline,
        skill="religion",
        language="vi",
        target_minutes=2,
        density="standard",
        providers=dict(STUB_PROVIDERS),
        voice=VoiceConfig(provider="stub-voice", voice_id="v"),
        image_style=ImageStyle(preset_id="cinematic", base_prompt="oil painting", aspect="16:9"),
    )
    return spec


# --------------------------------------------------------------------------- #
# 1. Pipeline tracer-bullet — REAL video                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")
async def test_tracer_bullet_pipeline_assembles_real_video(tmp_path, monkeypatch):
    registry = get_registry()  # real registry; stub-* are registered (keyless)
    ctx = _ctx()

    # ---- approve: build the series shell (status draft, segments empty) -----
    spec = _build_spec()
    assert len(spec.episodes) == 1
    ep = spec.episodes[0]
    assert ep.status == "draft" and ep.segments == []

    # ---- lazy script: Module 1 generate_episode_script via stub-script ------
    scripted = await generate_episode_script(spec, ep, ctx, registry=registry)
    assert scripted.status == "scripted"
    assert len(scripted.segments) >= 1
    assert [s.index for s in scripted.segments] == list(
        range(1, len(scripted.segments) + 1)
    )  # contiguous
    spec.episodes = [scripted]

    # ---- produce: Module 2 runner with REAL ffmpeg + REAL local storage -----
    storage_root = tmp_path / "storage"
    storage = LocalObjectStorage(root=storage_root, base_url="http://localhost:8000")
    ep_row = _EpRow(scripted.episode_id)
    jobs_store: dict = {}
    runner = _wire_runner(
        monkeypatch,
        spec=spec,
        scripted=scripted,
        ep_row=ep_row,
        jobs_store=jobs_store,
        storage=storage,
    )

    result = await runner.run_produce_episode(
        FAKE_USER,
        scripted.episode_id,
        ctx,
        registry=registry,
        work_root=tmp_path / "work",
    )

    # ---- assert: assembled + a REAL final.mp4 ffprobe can read --------------
    assert result["status"] == "assembled"
    assert result["images"] == len(scripted.segments)
    assert ep_row.status == "assembled"
    assert "final" in result["paths"]

    # The runner kept the whole project folder; the final.mp4 is a real video.
    work_dirs = list((tmp_path / "work").glob(f"{FAKE_USER}_*"))
    assert work_dirs, "project folder missing"
    proj = work_dirs[0]
    final_mp4 = proj / "final.mp4"
    subs_srt = proj / "subs.srt"
    thumbs = sorted((proj / "thumbnails").glob("*.png"))

    assert final_mp4.is_file() and final_mp4.stat().st_size > 0
    assert subs_srt.is_file() and subs_srt.read_text(encoding="utf-8").strip()
    assert len(thumbs) == 3, f"expected 3 thumbnails, got {len(thumbs)}"

    # ffprobe reads it back as h264 video + aac audio with a positive duration.
    duration = await ffmpeg.probe_duration(final_mp4)
    assert duration > 0
    codecs = await ffmpeg.run(
        [
            ffmpeg.ffprobe_bin(),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name",
            "-of",
            "csv=p=0",
            str(final_mp4),
        ]
    )
    assert "video" in codecs and "audio" in codecs

    # ---- export: signed URL (what POST /publish/export returns) -------------
    final_key = result["paths"]["final"]
    assert await storage.exists(final_key)
    video_url = await storage.signed_url(final_key)
    assert video_url.endswith(f"/files/{final_key}") or "/files/" in video_url
    assert result["paths"].get("srt")
    assert result["paths"].get("thumbnails")

    # ---- usage was recorded for every stub call (script + voice + N images) -
    events = ctx.usage._sink.events  # type: ignore[attr-defined]
    providers_used = {e.provider for e in events}
    assert {"stub-script", "stub-voice", "stub-image"} <= providers_used


# --------------------------------------------------------------------------- #
# 2. HTTP e2e — endpoints wired (no Redis/Postgres required)                  #
# --------------------------------------------------------------------------- #
def test_tracer_bullet_http_endpoints_wired(client):
    """TestClient with auth overridden: providers + wizard message resolve stubs.

    Proves the FastAPI surface is wired (auth dependency, router mounting,
    request/response schemas) and that Phase A honours a Setup-selected
    keyless provider end-to-end with no real key. ``client`` overrides
    ``get_current_user`` (see conftest) so no Google login is needed.
    """
    # /providers derives from services.yaml (Module 3) — stubs are intentionally
    # excluded from the public list, but the real free/paid providers appear.
    resp = client.get("/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert {"script", "image", "voice"} <= set(body)

    # /wizard/message — Phase A refine chat. Force the keyless stub-script
    # provider via the new optional Setup fields so it runs with no API key.
    resp = client.post(
        "/wizard/message",
        json={
            "idea": "A 3-episode series about world religions",
            "history": [],
            "skill": "religion",
            "language": "vi",
            "provider": "stub-script",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "reply" in data  # outline may be None (stub does not emit the block)


async def test_tracer_bullet_worker_task_inline(tmp_path, monkeypatch):
    """The Arq ``generate_script`` task body runs inline (no Redis) over a fake DB.

    Drives ``worker.tasks.generate_script`` end-to-end by patching its
    persistence seams so it loads a stub-* series, scripts it via the registry,
    and reports ``scripted`` — proving the worker entrypoint is wired without a
    real queue or database.
    """
    import worker.tasks as tasks

    spec = _build_spec()
    ep = spec.episodes[0]

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield object()

    captured: dict = {}

    async def fake_find(session, user_id, episode_id):
        return (object(), spec, ep)

    async def fake_update(session, user_id, series_id, updated):
        captured["updated"] = updated
        return spec

    # Build a real CallContext but skip the DB keystore preload (empty store).
    async def fake_build_ctx(ctx, user_id):
        return _ctx()

    async def fake_flush(call_ctx):
        return 0

    monkeypatch.setattr(tasks, "session_scope", fake_scope)
    monkeypatch.setattr(tasks, "build_call_context", fake_build_ctx)
    monkeypatch.setattr(tasks, "flush_call_context_usage", fake_flush)
    import module1.persistence as pers

    monkeypatch.setattr(pers, "find_series_for_episode", fake_find)
    monkeypatch.setattr(pers, "update_episode_in_series", fake_update)

    out = await tasks.generate_script({}, FAKE_USER, ep.episode_id)
    assert out["status"] == "scripted"
    assert out["segments"] >= 1
    assert captured["updated"].status == "scripted"
