"""Resume / idempotent produce (A): skip assets that are unchanged + still cached.

Two layers:
- Pure hash/reuse unit tests (no ffmpeg) over a real LocalObjectStorage.
- A runner-level test that asserts a second produce run with an unchanged spec
  skips every paid image call (child jobs marked done from cache), regenerates a
  segment whose prompt changed, and regenerates a segment whose cached file is
  missing.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from clients.base import (
    AIClient,
    CallContext,
    ImageRequest,
    ImageResult,
    ServiceConfig,
    Task,
    VoiceRequest,
    VoiceResult,
)
from keystore import Cipher, KeyStore
from models.spec import (
    EpisodeSpec,
    ImageStyle,
    SegmentSpec,
    SeriesSpec,
    VoiceConfig,
)
from module2 import materialize as mat
from module2 import resume as resume_mod
from storage import episode_key
from storage.local import LocalObjectStorage
from usage import UsageLogger


# --------------------------------------------------------------------------- #
# Fixtures / fakes                                                            #
# --------------------------------------------------------------------------- #
def _series() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="Faiths", topic="religion", skill="religion",
        language="vi", target_minutes=5, density="standard",
        providers={"script": "stub-script", "image": "stub-image", "voice": "stub-voice"},
        image_style=ImageStyle(preset_id="p", base_prompt="oil painting", aspect="16:9"),
        voice=VoiceConfig(provider="stub-voice", voice_id="v"),
    )


def _episode(n: int = 2) -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="e1", title="Origins", order=1, status="scripted",
        segments=[
            SegmentSpec(
                index=i, narration=f"Scene {i} narration words here",
                image_prompt=f"scene {i}", image_label=f"scene{i}",
            )
            for i in range(1, n + 1)
        ],
    )


# --------------------------------------------------------------------------- #
# Hashing                                                                     #
# --------------------------------------------------------------------------- #
def test_image_hash_changes_with_prompt():
    series, ep = _series(), _episode(1)
    seg = ep.segments[0]
    h1 = resume_mod.image_hash(series, seg)
    seg2 = SegmentSpec(
        index=seg.index, narration=seg.narration,
        image_prompt="a totally different scene", image_label=seg.image_label,
    )
    assert resume_mod.image_hash(series, seg2) != h1
    # Same content -> same hash (deterministic).
    assert resume_mod.image_hash(series, seg) == h1


def test_voice_hash_changes_with_narration():
    series, ep = _series(), _episode(2)
    h1 = resume_mod.voice_hash(series, ep)
    ep.segments[0].narration = "rewritten narration"
    assert resume_mod.voice_hash(series, ep) != h1


def test_thumbnail_hash_changes_with_title():
    series, ep = _series(), _episode(1)
    h1 = resume_mod.thumbnail_hash(series, ep)
    ep.title = "A New Title"
    assert resume_mod.thumbnail_hash(series, ep) != h1


def test_build_and_read_manifest_roundtrip():
    series, ep = _series(), _episode(2)
    man = resume_mod.build_manifest(series, ep)
    assert set(man["images"]) == {"1", "2"}
    assert man["voice"] == resume_mod.voice_hash(series, ep)
    # Round-trips through the paths JSONB blob.
    paths = {"final": "k", resume_mod.MANIFEST_KEY: man}
    assert resume_mod.read_manifest(paths) == man
    assert resume_mod.read_manifest({}) == {}
    assert resume_mod.read_manifest(None) == {}


# --------------------------------------------------------------------------- #
# Reuse helpers over a real LocalObjectStorage                                #
# --------------------------------------------------------------------------- #
def _patch_storage(monkeypatch, store):
    monkeypatch.setattr(resume_mod, "get_storage", lambda: store)


async def test_reuse_segment_image_hit_when_cached_and_unchanged(tmp_path, monkeypatch):
    store = LocalObjectStorage(root=tmp_path / "store")
    _patch_storage(monkeypatch, store)
    series, ep = _series(), _episode(1)
    seg = ep.segments[0]
    lo = mat.layout_for(tmp_path / "work")
    for d in (lo.images_dir, lo.voice_dir, lo.thumbnails_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Seed the cached PNG in storage at the deterministic key.
    rel = f"images/{mat.image_filename(seg.index, seg.image_label)}.png"
    await store.put(episode_key("u", "e1", *rel.split("/")), b"PNGDATA")

    want = resume_mod.image_hash(series, seg)
    prev = {"images": {"1": want}}
    kind = await resume_mod.reuse_segment_image(
        "u", "e1", seg, lo, want_hash=want, prev=prev, curation=None
    )
    assert kind == "image"
    assert lo.image_png(seg.index, seg.image_label).read_bytes() == b"PNGDATA"


async def test_reuse_segment_image_miss_when_hash_differs(tmp_path, monkeypatch):
    store = LocalObjectStorage(root=tmp_path / "store")
    _patch_storage(monkeypatch, store)
    series, ep = _series(), _episode(1)
    seg = ep.segments[0]
    lo = mat.layout_for(tmp_path / "work")
    lo.images_dir.mkdir(parents=True, exist_ok=True)
    rel = f"images/{mat.image_filename(seg.index, seg.image_label)}.png"
    await store.put(episode_key("u", "e1", *rel.split("/")), b"PNGDATA")

    # Manifest carries a DIFFERENT (stale) hash -> no reuse, must regenerate.
    prev = {"images": {"1": "stale-hash"}}
    want = resume_mod.image_hash(series, seg)
    kind = await resume_mod.reuse_segment_image(
        "u", "e1", seg, lo, want_hash=want, prev=prev, curation=None
    )
    assert kind is None


async def test_reuse_segment_image_miss_when_file_absent(tmp_path, monkeypatch):
    store = LocalObjectStorage(root=tmp_path / "store")
    _patch_storage(monkeypatch, store)
    series, ep = _series(), _episode(1)
    seg = ep.segments[0]
    lo = mat.layout_for(tmp_path / "work")
    lo.images_dir.mkdir(parents=True, exist_ok=True)

    # Hash matches but nothing is in storage -> regenerate.
    want = resume_mod.image_hash(series, seg)
    prev = {"images": {"1": want}}
    kind = await resume_mod.reuse_segment_image(
        "u", "e1", seg, lo, want_hash=want, prev=prev, curation=None
    )
    assert kind is None


# --------------------------------------------------------------------------- #
# Runner-level: second run skips paid image calls                            #
# --------------------------------------------------------------------------- #
class _CountingImageClient(AIClient):
    """Records every generate_image call (a proxy for paid kie/gemini credit)."""

    capabilities = {Task.GENERATE_IMAGE}
    requires_key = False

    def __init__(self, config):
        super().__init__(config)
        self.calls: list[int] = []

    async def is_available(self, ctx):
        return True

    async def generate_image(self, req: ImageRequest, out_path: Path, ctx) -> ImageResult:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"IMG")
        self.calls.append(1)
        return ImageResult(out_path=out, count=1)


class _CountingVoiceClient(AIClient):
    capabilities = {Task.GENERATE_VOICE}
    requires_key = False

    def __init__(self, config):
        super().__init__(config)
        self.calls = 0

    async def is_available(self, ctx):
        return True

    async def generate_voice(self, req: VoiceRequest, out_path: Path, ctx) -> VoiceResult:
        self.calls += 1
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"MP3")
        return VoiceResult(out_path=out, chars=len(req.text or ""))


class _Registry:
    def __init__(self, *, image, voice):
        self._by_task = {Task.GENERATE_IMAGE: image, Task.GENERATE_VOICE: voice}

    async def resolve(self, task, preferred, ctx):
        return self._by_task[task]


class _JobRow:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _FakeJobRepo:
    def __init__(self, store):
        self.store = store
        self.s = self

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


class _EpRow:
    def __init__(self, ep_id, paths=None):
        self.id = ep_id
        self.status = "scripted"
        self.paths = dict(paths or {})


class _FakeEpisodeRepo:
    def __init__(self, ep_row):
        self.ep_row = ep_row

    async def get(self, user_id, episode_id):
        return self.ep_row if self.ep_row.id == episode_id else None

    async def set_status(self, user_id, episode_id, status):
        row = await self.get(user_id, episode_id)
        if row is not None:
            row.status = status

    async def set_paths(self, user_id, episode_id, paths, *, urls=None, status=None, merge=True):
        row = await self.get(user_id, episode_id)
        if row is None:
            return None
        row.paths = {**(row.paths or {}), **paths} if merge else dict(paths)
        if status is not None:
            row.status = status
        return row

    async def get_curation(self, user_id, episode_id):
        return None


def _patch_runner(monkeypatch, jobs_store, ep_row, storage, series, ep):
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
    monkeypatch.setattr(resume_mod, "get_storage", lambda: storage)

    async def fake_find_parent(repo, user_id, episode_id):
        return None

    monkeypatch.setattr(runner.jobmod, "find_parent_for_episode", fake_find_parent)

    async def fake_ensure(user_id, episode_id, ctx):
        return series, ep

    monkeypatch.setattr(runner, "ensure_scripted", fake_ensure)

    # Render is CPU/ffmpeg — fake it (write final.mp4) so the test needs no ffmpeg.
    async def fake_render(media_paths, narrations, voice_mp3, final_mp4, aspect, **kw):
        Path(final_mp4).write_bytes(b"FINAL")

    monkeypatch.setattr(runner.renderer, "render_episode", fake_render)

    # synth_voice writes the file + reports duration (no real ffmpeg concat/probe).
    async def fake_synth(series_, lo, ctx, *, registry):
        lo.voice_mp3.parent.mkdir(parents=True, exist_ok=True)
        lo.voice_mp3.write_bytes(b"MP3")
        from module2.voice import VoiceOutcome

        await registry.resolve(Task.GENERATE_VOICE, series_.voice.provider, ctx)
        return VoiceOutcome(voice_mp3=lo.voice_mp3, duration_s=5.0, parts=[], total_chars=10)

    monkeypatch.setattr(runner.voice, "synth_voice", fake_synth)
    # Probe of the cached voice on resume — fake (no ffmpeg).
    from module2 import ffmpeg as _ffmpeg

    async def fake_probe(path):
        return 5.0

    monkeypatch.setattr(_ffmpeg, "probe_duration", fake_probe)

    # subtitles + thumbnails are best-effort; stub to avoid extra deps.
    monkeypatch.setattr(runner.subtitles, "write_srt", lambda *a, **k: None)

    async def fake_thumbs(*a, **k):
        return []

    monkeypatch.setattr(runner.thumbnail, "generate_thumbnails", fake_thumbs)
    return runner


def _ctx():
    return CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())


async def test_second_run_skips_unchanged_images(tmp_path, monkeypatch):
    """Run produce twice; the 2nd run reuses cached images (no new paid calls)."""
    series, ep = _series(), _episode(2)
    ep_row = _EpRow("e1")
    storage = LocalObjectStorage(root=tmp_path / "store")
    img = _CountingImageClient(ServiceConfig("stub-image", {}))
    voice = _CountingVoiceClient(ServiceConfig("stub-voice", {}))
    reg = _Registry(image=img, voice=voice)

    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)

    # Run 1: full generation (2 image calls), manifest persisted on ep_row.paths.
    res1 = await runner.run_produce_episode(
        "u", "e1", _ctx(), registry=reg, work_root=tmp_path / "w1"
    )
    assert res1["status"] == "assembled"
    assert len(img.calls) == 2
    assert resume_mod.MANIFEST_KEY in ep_row.paths

    # Run 2: spec unchanged + assets cached → reuse, NO new image calls.
    img.calls.clear()
    jobs2: dict = {}
    runner = _patch_runner(monkeypatch, jobs2, ep_row, storage, series, ep)
    res2 = await runner.run_produce_episode(
        "u", "e1", _ctx(), registry=reg, work_root=tmp_path / "w2"
    )
    assert res2["status"] == "assembled"
    assert len(img.calls) == 0  # every image reused from cache (no paid spend)
    # All image child jobs marked done (reused) so the UI shows progress instantly.
    img_states = [r.state for r in jobs2.values() if r.kind == "image"]
    assert img_states == ["done", "done"]


async def test_second_run_regenerates_changed_segment(tmp_path, monkeypatch):
    """A segment whose prompt changed is regenerated; the unchanged one is reused."""
    series, ep = _series(), _episode(2)
    ep_row = _EpRow("e1")
    storage = LocalObjectStorage(root=tmp_path / "store")
    img = _CountingImageClient(ServiceConfig("stub-image", {}))
    voice = _CountingVoiceClient(ServiceConfig("stub-voice", {}))
    reg = _Registry(image=img, voice=voice)

    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)
    await runner.run_produce_episode("u", "e1", _ctx(), registry=reg, work_root=tmp_path / "w1")
    assert len(img.calls) == 2

    # Change segment 2's image prompt only.
    img.calls.clear()
    ep.segments[1].image_prompt = "a brand new scene for segment two"
    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)
    await runner.run_produce_episode("u", "e1", _ctx(), registry=reg, work_root=tmp_path / "w2")
    # Exactly ONE regenerate (the changed segment); the other was reused.
    assert len(img.calls) == 1


async def test_second_run_regenerates_when_cache_missing(tmp_path, monkeypatch):
    """Manifest matches but the cached file vanished → regenerate that segment."""
    series, ep = _series(), _episode(2)
    ep_row = _EpRow("e1")
    storage = LocalObjectStorage(root=tmp_path / "store")
    img = _CountingImageClient(ServiceConfig("stub-image", {}))
    voice = _CountingVoiceClient(ServiceConfig("stub-voice", {}))
    reg = _Registry(image=img, voice=voice)

    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)
    await runner.run_produce_episode("u", "e1", _ctx(), registry=reg, work_root=tmp_path / "w1")
    assert len(img.calls) == 2

    # Delete segment 1's cached PNG from storage (deploy wiped it / partial upload).
    rel = f"images/{mat.image_filename(1, ep.segments[0].image_label)}.png"
    await storage.delete(episode_key("u", "e1", *rel.split("/")))

    img.calls.clear()
    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)
    await runner.run_produce_episode("u", "e1", _ctx(), registry=reg, work_root=tmp_path / "w2")
    # Only the missing one is regenerated.
    assert len(img.calls) == 1


async def test_crash_midrun_then_resume_only_regenerates_unfinished(tmp_path, monkeypatch):
    """The user's case: a run dies after SOME images finished (e.g. a deploy killed
    the worker). Because each finished image is persisted immediately (incremental,
    A), a re-run reuses them from storage and only the unfinished segment is
    (re)generated — no re-spent image credit on work already done."""
    series, ep = _series(), _episode(3)
    ep_row = _EpRow("e1")
    storage = LocalObjectStorage(root=tmp_path / "store")
    voice = _CountingVoiceClient(ServiceConfig("stub-voice", {}))

    # Run 1: image client that FAILS on segment 2. Segments 1 and 3 run in parallel
    # and DO finish (and are persisted) before the run raises (render blocked, M2-7).
    class _FailSeg2(_CountingImageClient):
        async def generate_image(self, req, out_path, ctx):
            if req.label == "scene2":
                self.calls.append(2)
                raise RuntimeError("kie 500 on segment 2")
            return await super().generate_image(req, out_path, ctx)

    img1 = _FailSeg2(ServiceConfig("stub-image", {}))
    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)
    with pytest.raises(RuntimeError):
        await runner.run_produce_episode(
            "u", "e1", _ctx(), registry=_Registry(image=img1, voice=voice),
            work_root=tmp_path / "w1",
        )

    # The two finished images were made durable mid-run; segment 2 was not.
    persisted = set((ep_row.paths.get(resume_mod.MANIFEST_KEY, {}).get("images") or {}).keys())
    assert persisted == {"1", "3"}
    assert ep_row.paths[resume_mod.MANIFEST_KEY].get("voice")  # voice persisted too

    # Run 2 with a HEALTHY client: only segment 2 (the unfinished one) regenerates;
    # segments 1 + 3 are reused from storage (no new paid image calls).
    img2 = _CountingImageClient(ServiceConfig("stub-image", {}))
    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)
    res = await runner.run_produce_episode(
        "u", "e1", _ctx(), registry=_Registry(image=img2, voice=voice),
        work_root=tmp_path / "w2",
    )
    assert res["status"] == "assembled"
    assert len(img2.calls) == 1  # ONLY segment 2 regenerated (not all 3)


class _FakeKieClient(AIClient):
    """A kie-like client: async tasks, submit returns ``t-<label>``, counts submits."""

    capabilities = {Task.GENERATE_IMAGE}
    requires_key = False
    supports_async_image_tasks = True

    def __init__(self, config, *, crash_poll_ids=()):
        super().__init__(config)
        self.submits: list[str] = []  # labels submitted (proxy for spent credit)
        self.crash_poll_ids = set(crash_poll_ids)

    async def is_available(self, ctx):
        return True

    async def submit_image_task(self, req: ImageRequest, ctx) -> str:
        self.submits.append(req.label or "")
        return f"t-{req.label}"

    async def poll_image_task(self, task_id, out_path, ctx, *, max_wait=300, poll_interval=5):
        if task_id in self.crash_poll_ids:
            raise RuntimeError(f"worker died polling {task_id}")
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"IMG")
        return ImageResult(out_path=out, count=1, raw={"task_id": task_id})


async def test_resume_fetches_inflight_kie_task_without_resubmit(tmp_path, monkeypatch):
    """The taskId optimization: an image still generating on kie when the worker
    died is FETCHED by its persisted taskId on resume (recordInfo) — not
    re-submitted, so no re-spent credit on a task kie is already running."""
    series, ep = _series(), _episode(2)
    ep_row = _EpRow("e1")
    storage = LocalObjectStorage(root=tmp_path / "store")
    voice = _CountingVoiceClient(ServiceConfig("stub-voice", {}))

    # Run 1: segment 2's poll dies (worker killed) AFTER its taskId was persisted;
    # segment 1 finishes + is uploaded. The run raises (render blocked, M2-7).
    kie1 = _FakeKieClient(ServiceConfig("kie", {}), crash_poll_ids={"t-scene2"})
    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)
    with pytest.raises(RuntimeError):
        await runner.run_produce_episode(
            "u", "e1", _ctx(), registry=_Registry(image=kie1, voice=voice),
            work_root=tmp_path / "w1",
        )
    assert kie1.submits == ["scene1", "scene2"]  # both tasks created on run 1
    # Both taskIds persisted at submit; segment 2 was NOT uploaded (poll crashed).
    kie_tasks = ep_row.paths.get(resume_mod.KIE_TASKS_KEY, {})
    assert set(kie_tasks) == {"1", "2"}
    man_imgs = ep_row.paths.get(resume_mod.MANIFEST_KEY, {}).get("images") or {}
    assert "1" in man_imgs and "2" not in man_imgs

    # Run 2: healthy kie. Segment 1 reused from STORAGE; segment 2 FETCHED by its
    # persisted taskId. Crucially, NO new submit happens (zero re-spent credit).
    kie2 = _FakeKieClient(ServiceConfig("kie", {}))
    runner = _patch_runner(monkeypatch, {}, ep_row, storage, series, ep)
    res = await runner.run_produce_episode(
        "u", "e1", _ctx(), registry=_Registry(image=kie2, voice=voice),
        work_root=tmp_path / "w2",
    )
    assert res["status"] == "assembled"
    assert kie2.submits == []  # neither segment re-submitted → no new kie credit
