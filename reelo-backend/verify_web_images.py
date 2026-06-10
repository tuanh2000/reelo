"""End-to-end verification: web-commons real photos THROUGH the Reelo pipeline.

Unlike the now-removed standalone ``make_horseshoe_video.py`` driver (which
called Commons + ffmpeg directly, bypassing the system), this proves
``web-commons`` is a *system feature*: it goes through the REAL
``ServiceRegistry.resolve`` and the REAL Module 2 ``run_produce_episode``
runner, with:

    providers.image = "web-commons"   (resolved via registry, keyless)
    providers.script = "stub-script"  (keyless; deterministic segments)
    providers.voice  = "stub-voice"   (keyless; real silent MP3 via ffmpeg)
    render           = real ffmpeg

Output: a ``final.mp4`` assembled from REAL Public-Domain / CC-BY photos pulled
from Wikimedia Commons, plus ``credits.json`` with per-image attribution.

Two modes:

- **auto** (default): the runner auto-picks one photo per segment.
- **curated** (``--curated`` / ``REELO_VERIFY_CURATED=1``): the human-curation
  path (M2-12) — ``search_candidates`` returns a real candidate grid, we pick one
  per segment (the first), build an ``image_curation`` blob, and the runner
  downloads the *chosen* photos via ``download_chosen``. Proves the curated photo
  (not an auto pick) ends up in the mp4.

Run::

    cd reelo-backend
    .venv/bin/python verify_web_images.py            # auto mode (default)
    .venv/bin/python verify_web_images.py --curated  # human-curation mode (M2-12)
    REELO_VERIFY_OUT=/tmp/web-verify .venv/bin/python verify_web_images.py

Requires: network access to commons.wikimedia.org and ffmpeg/ffprobe on PATH.
It exits 0 on success (mp4 + attribution), 77 (skip) if prerequisites are
missing, non-zero on failure. It is intentionally a script (not a pytest test)
because it makes live network calls; the offline coverage lives in
``tests/test_web_commons_image.py`` and ``tests/test_tracer_bullet.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from pathlib import Path

import httpx

from clients.base import CallContext
from clients.commons_image import COMMONS_API, USER_AGENT
from clients.registry import get_registry
from keystore import Cipher, KeyStore
from models.spec import EpisodeSpec, ImageStyle, SegmentSpec, SeriesSpec, VoiceConfig
from module2 import ffmpeg
from usage import UsageLogger

FAKE_USER = "u_verify"
SKIP = 77

# A small real episode: English narration + concrete image_query per segment so
# Commons returns matching real photos.
_SEGMENTS = [
    ("On dark beaches lives a creature older than the dinosaurs: the horseshoe crab.",
     "Atlantic horseshoe crab beach", "01_intro"),
    ("Its blue, copper-based blood clots on contact with bacterial toxins.",
     "horseshoe crab underside anatomy", "02_blood"),
    ("Every spring, migrating red knots survive by eating horseshoe crab eggs.",
     "red knot bird flock Delaware", "03_redknot"),
]


def _ctx() -> CallContext:
    return CallContext(
        user_id=FAKE_USER, keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger(), extra={}
    )


def _build_spec(out_root: Path) -> tuple[SeriesSpec, EpisodeSpec]:
    segments = [
        SegmentSpec(index=i, narration=n, image_prompt=f"real photo of {q}",
                    image_label=label, image_query=q)
        for i, (n, q, label) in enumerate(_SEGMENTS, start=1)
    ]
    ep = EpisodeSpec(
        episode_id="e_web", title="Horseshoe crab", order=1, status="scripted",
        segments=segments,
    )
    spec = SeriesSpec(
        series_id="s_web", name="Real Photos", topic="horseshoe crab", skill="explain",
        language="en", target_minutes=1, density="standard",
        providers={"script": "stub-script", "image": "web-commons", "voice": "stub-voice"},
        image_style=ImageStyle(preset_id="documentary", base_prompt="documentary photo",
                               aspect="16:9"),
        voice=VoiceConfig(provider="stub-voice", voice_id="stub-voice"),
        episodes=[ep],
    )
    return spec, ep


def _network_ok() -> bool:
    try:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as c:
            r = c.get(COMMONS_API, params={"action": "query", "format": "json",
                                           "meta": "siteinfo"}, timeout=10)
            return r.status_code == 200
    except httpx.HTTPError:
        return False


# --------------------------------------------------------------------------- #
# Wire the runner's DB/storage seams to in-memory fakes (no Postgres/Redis).   #
# --------------------------------------------------------------------------- #
class _EpRow:
    def __init__(self, ep_id: str) -> None:
        self.id = ep_id
        self.user_id = FAKE_USER
        self.status = "scripted"
        self.paths: dict = {}
        self.urls: dict = {}


def _patch_runner(spec, ep, ep_row, storage, curation=None):
    import module2.runner as runner

    class _FakeJobRepo:
        def __init__(self, *a, **k):
            self.store: dict = {}
            self.s = self

        async def flush(self):
            return None

        async def add(self, row):
            self.store[row.id] = row
            return row

        async def get(self, user_id, job_id):
            row = self.store.get(job_id)
            return row if row and row.user_id == user_id else None

    job_repo = _FakeJobRepo()

    class _FakeEpisodeRepo:
        def __init__(self, *a, **k):
            pass

        async def set_paths(self, user_id, episode_id, paths, *, urls=None, status=None, merge=True):
            ep_row.paths = {**ep_row.paths, **paths}
            if status:
                ep_row.status = status
            return ep_row

        async def get_curation(self, user_id, episode_id):
            return curation

    @contextlib.asynccontextmanager
    async def fake_scope():
        yield object()

    runner.session_scope = fake_scope
    runner.GenJobRepo = lambda s: job_repo
    runner.EpisodeRepo = lambda s: _FakeEpisodeRepo()
    runner.get_storage = lambda: storage

    async def fake_ensure(user_id, episode_id, ctx):
        return spec, ep

    runner.ensure_scripted = fake_ensure

    async def fake_find_parent(repo, user_id, episode_id):
        return None

    runner.jobmod.find_parent_for_episode = fake_find_parent
    return runner


async def main() -> int:
    if not ffmpeg.ffmpeg_available():
        print("SKIP: ffmpeg/ffprobe not on PATH")
        return SKIP
    if not _network_ok():
        print("SKIP: cannot reach commons.wikimedia.org (offline?)")
        return SKIP

    from storage.local import LocalObjectStorage

    out_root = Path(os.environ.get("REELO_VERIFY_OUT") or
                    (Path(__file__).resolve().parent.parent / "output" / "web-verify"))
    out_root.mkdir(parents=True, exist_ok=True)
    work_root = out_root / "work"
    storage_root = out_root / "storage"

    registry = get_registry()  # REAL registry; web-commons is registered keyless
    ctx = _ctx()
    spec, ep = _build_spec(out_root)
    ep_row = _EpRow(ep.episode_id)
    storage = LocalObjectStorage(root=storage_root, base_url="http://localhost:8000")

    curated = ("--curated" in sys.argv) or (os.environ.get("REELO_VERIFY_CURATED") == "1")
    curation = None
    if curated:
        from module2 import curation as cur

        client = registry.get(spec.providers["image"])
        print("== Building curation (search_candidates) — human picks the first photo ==")
        curation = await cur.build_curation(
            registry, client, ep, ctx, size=spec.image_style.aspect
        )
        for seg in curation["segments"]:
            n = len(seg["candidates"])
            print(f"   seg{seg['index']}: {n} candidate(s); chosen={seg['chosen_id']!r}")
            if n < 1:
                print("FAIL: a segment returned 0 candidates (expected ≥1)")
                return 1

    runner = _patch_runner(spec, ep, ep_row, storage, curation=curation)

    print("== Producing episode through registry.resolve + module2.runner ==")
    print(f"   mode: {'curated (M2-12)' if curated else 'auto'}")
    print(f"   image provider: {spec.providers['image']} (resolved via registry)")
    result = await runner.run_produce_episode(
        FAKE_USER, ep.episode_id, ctx, registry=registry, work_root=work_root
    )

    # ---- assertions ----
    proj_dirs = list(work_root.glob(f"{FAKE_USER}_*"))
    if not proj_dirs:
        print("FAIL: project folder missing")
        return 1
    proj = proj_dirs[0]
    final_mp4 = proj / "final.mp4"
    credits_path = proj / "credits.json"
    images = sorted((proj / "images").glob("*.jpg")) + sorted((proj / "images").glob("*.png"))

    ok = True
    if not (final_mp4.is_file() and final_mp4.stat().st_size > 0):
        print("FAIL: final.mp4 missing/empty")
        ok = False
    else:
        dur = await ffmpeg.probe_duration(final_mp4)
        w, h = await ffmpeg.probe_dimensions(final_mp4)
        print(f"   final.mp4: {dur:.1f}s, {w}x{h}, {final_mp4.stat().st_size // 1024} KB")

    if not credits_path.is_file():
        print("FAIL: credits.json missing (attribution not persisted)")
        ok = False
    else:
        credits = json.loads(credits_path.read_text(encoding="utf-8"))
        print(f"   credits.json: {len(credits['images'])} real photos with attribution")
        for c in credits["images"]:
            print(f"     - seg{c['index']}: {c['title']}  [{c['license']}]  by {c['author']}")
        if "credits" not in result["paths"]:
            print("FAIL: credits key not in uploaded paths")
            ok = False

    if len(images) != len(ep.segments):
        print(f"FAIL: expected {len(ep.segments)} images, got {len(images)}")
        ok = False

    if ok:
        print(f"\nOK: real-photo video assembled via the pipeline -> {final_mp4}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
