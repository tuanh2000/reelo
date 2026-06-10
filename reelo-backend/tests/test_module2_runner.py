"""produce_episode runner end-to-end with stub clients + in-memory DB/storage.

Uses real ffmpeg when available (stub-image PNGs + a real silent MP3 voice
client); when ffmpeg is absent, the render step is monkeypatched so the
orchestration (job state machine, invariant gate, upload, status) is still
exercised.
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
from clients.stub import PlaceholderImageClient
from keystore import Cipher, KeyStore
from models.spec import (
    EpisodeSpec,
    ImageStyle,
    SegmentSpec,
    SeriesSpec,
    VoiceConfig,
)
from module2 import ffmpeg
from usage import UsageLogger

FFMPEG = ffmpeg.ffmpeg_available()


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #
class _RealMp3VoiceClient(AIClient):
    """Voice client that writes a genuinely-valid silent MP3 (via ffmpeg lavfi).

    The stub SilentVoiceClient writes a raw frame newer ffmpeg can't probe, so
    the runner e2e (which probes duration + concats) uses this when ffmpeg is on.
    """

    capabilities = {Task.GENERATE_VOICE}
    requires_key = False

    async def is_available(self, ctx: CallContext) -> bool:
        return True

    async def generate_voice(self, req: VoiceRequest, out_path: Path, ctx: CallContext) -> VoiceResult:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        await ffmpeg.run([
            ffmpeg.ffmpeg_bin(), "-y", "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=mono", "-t", "1",
            "-c:a", "libmp3lame", "-b:a", "128k", str(out),
        ])
        return VoiceResult(out_path=out, chars=len(req.text or ""))


class _FailingImageClient(AIClient):
    capabilities = {Task.GENERATE_IMAGE}
    requires_key = False

    async def is_available(self, ctx: CallContext) -> bool:
        return True

    async def generate_image(self, req: ImageRequest, out_path: Path, ctx: CallContext) -> ImageResult:
        raise RuntimeError("image provider down")


class _Registry:
    """Resolve by task to a configured client."""

    def __init__(self, *, image: AIClient, voice: AIClient) -> None:
        self._by_task = {Task.GENERATE_IMAGE: image, Task.GENERATE_VOICE: voice}

    async def resolve(self, task, preferred, ctx):
        return self._by_task[task]


class _FakeJobRepo:
    """In-memory GenJobRepo over a shared dict of rows."""

    def __init__(self, store: dict) -> None:
        self.store = store
        # mimic the .s.flush() the runner calls
        self.s = self

    async def flush(self):
        return None

    async def execute(self, *a, **k):  # for find_parent_for_episode path
        raise AssertionError("execute should not be called in fake")

    async def add(self, row):
        self.store[row.id] = row
        return row

    async def get(self, user_id, job_id):
        row = self.store.get(job_id)
        return row if row and row.user_id == user_id else None

    async def children_for_episode(self, user_id, episode_id):
        return [
            r for r in self.store.values()
            if r.episode_id == episode_id and r.user_id == user_id and r.parent_id is not None
        ]


class _FakeEpisodeRepo:
    def __init__(self, ep_row) -> None:
        self.ep_row = ep_row

    async def get(self, user_id, episode_id):
        return self.ep_row if self.ep_row.id == episode_id else None

    async def set_status(self, user_id, episode_id, status):
        row = await self.get(user_id, episode_id)
        if row is not None:
            row.status_history = getattr(row, "status_history", [])
            row.status_history.append(status)
            row.status = status

    async def set_paths(self, user_id, episode_id, paths, *, urls=None, status=None, merge=True):
        row = await self.get(user_id, episode_id)
        if row is None:
            return None
        row.paths = {**(row.paths or {}), **paths} if merge else dict(paths)
        if status is not None:
            row.status_history = getattr(row, "status_history", [])
            row.status_history.append(status)
            row.status = status
        return row


class _FakeStorage:
    def __init__(self) -> None:
        self.uploaded: dict[str, Path] = {}

    async def put_file(self, key, path, **kw):
        self.uploaded[key] = Path(path)
        return key

    async def put(self, key, data, **kw):
        return key

    async def get_to_file(self, key, path):
        raise FileNotFoundError(key)


class _EpRow:
    def __init__(self, ep_id: str) -> None:
        self.id = ep_id
        self.status = "scripted"
        self.paths: dict = {}


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
                index=i, narration=f"Scene {i} narration with enough words here now today",
                image_prompt=f"scene {i}", image_label=f"scene{i}",
            )
            for i in range(1, n + 1)
        ],
    )


def _patch_common(monkeypatch, jobs_store, ep_row, storage, *, parent=None):
    """Patch session_scope, repos, persistence, storage in the runner."""
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

    # find_parent_for_episode iterates repo.s.execute; bypass by returning given parent
    async def fake_find_parent(repo, user_id, episode_id):
        return parent

    monkeypatch.setattr(runner.jobmod, "find_parent_for_episode", fake_find_parent)
    return job_repo, ep_repo


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg/ffprobe not on PATH")
async def test_runner_end_to_end_assembles(tmp_path, monkeypatch):
    series, ep = _series(), _episode(2)
    ep_row = _EpRow("e1")
    jobs_store: dict = {}
    storage = _FakeStorage()

    import module2.runner as runner

    _patch_common(monkeypatch, jobs_store, ep_row, storage)

    async def fake_ensure(user_id, episode_id, ctx):
        return series, ep

    monkeypatch.setattr(runner, "ensure_scripted", fake_ensure)

    reg = _Registry(
        image=PlaceholderImageClient(ServiceConfig("stub-image", {})),
        voice=_RealMp3VoiceClient(ServiceConfig("voice", {})),
    )
    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())

    result = await runner.run_produce_episode(
        "u", "e1", ctx, registry=reg, work_root=tmp_path / "work"
    )

    assert result["status"] == "assembled"
    assert result["images"] == 2
    assert "final" in result["paths"]
    # episode row flipped: assets (đang sản xuất) → assembled, in that order
    assert ep_row.status == "assembled"
    assert getattr(ep_row, "status_history", []) == ["assets", "assembled"]
    # all child jobs done
    states = {r.kind: r.state for r in jobs_store.values() if r.parent_id is not None}
    assert states["voice"] == "done"
    assert states["render"] == "done"
    assert states["thumbnail"] == "done"
    # final.mp4 + subs.srt + thumbnails uploaded
    keys = list(storage.uploaded)
    assert any(k.endswith("final.mp4") for k in keys)
    assert any(k.endswith("subs.srt") for k in keys)
    assert any("thumbnails/" in k for k in keys)


async def test_runner_blocks_render_on_image_failure(tmp_path, monkeypatch):
    series, ep = _series(), _episode(2)
    ep_row = _EpRow("e1")
    jobs_store: dict = {}
    storage = _FakeStorage()

    import module2.runner as runner

    _patch_common(monkeypatch, jobs_store, ep_row, storage)

    async def fake_ensure(user_id, episode_id, ctx):
        return series, ep

    monkeypatch.setattr(runner, "ensure_scripted", fake_ensure)

    # render must NOT be called when an image fails
    render_called = []

    async def fake_render(*a, **k):
        render_called.append(True)

    monkeypatch.setattr(runner.renderer, "render_episode", fake_render)

    # voice client writes a small file; ffmpeg not needed because render is faked,
    # but synth_voice still concats + probes -> fake those too.
    async def fake_synth(series_, lo, ctx, *, registry):
        lo.voice_mp3.parent.mkdir(parents=True, exist_ok=True)
        lo.voice_mp3.write_bytes(b"x")
        from module2.voice import VoiceOutcome
        return VoiceOutcome(voice_mp3=lo.voice_mp3, duration_s=5.0, parts=[], total_chars=10)

    monkeypatch.setattr(runner.voice, "synth_voice", fake_synth)

    reg = _Registry(
        image=_FailingImageClient(ServiceConfig("img", {})),
        voice=PlaceholderImageClient(ServiceConfig("v", {})),  # unused
    )
    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())

    with pytest.raises(RuntimeError):
        await runner.run_produce_episode(
            "u", "e1", ctx, registry=reg, work_root=tmp_path / "work"
        )

    assert render_called == []  # render was blocked (M2-7)
    states = {r.kind: r.state for r in jobs_store.values()}
    assert states["render"] == "error"
    assert states["parent"] == "error"
    # all image jobs errored
    img_states = [r.state for r in jobs_store.values() if r.kind == "image"]
    assert img_states == ["error", "error"]
