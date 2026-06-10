"""Media curation merge (M2-13) — mix web-commons photos + web-pexels clips.

Offline: fake clients (no httpx). Proves that with a Pexels "key" (a clip
provider available) one segment's grid has BOTH images and video clips, the
per-candidate ``provider`` is recorded, the chosen provider/media-type resolve,
and that without a clip provider the grid is photos-only (graceful).
"""

from __future__ import annotations

from pathlib import Path

from clients.base import CallContext, MediaCandidate
from keystore import Cipher, KeyStore
from module2 import curation as cur
from usage import UsageLogger


def _ctx() -> CallContext:
    return CallContext(user_id="u1", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger(), extra={})


class _PhotoClient:
    provider_id = "web-commons"
    supports_candidates = True
    requires_key = False

    async def is_available(self, ctx):
        return True

    async def search_candidates(self, query, ctx, *, size, limit, exclude):
        out = []
        for i in range(min(limit, 3)):
            cid = f"img-{i}"
            if cid in exclude:
                continue
            out.append(MediaCandidate(id=cid, thumb_url=f"it{i}", full_url=f"if{i}",
                                      title=cid, license="CC0", media_type="image"))
        return out


class _ClipClient:
    provider_id = "web-pexels"
    supports_candidates = True
    requires_key = True

    def __init__(self, available=True):
        self._available = available

    async def is_available(self, ctx):
        return self._available

    async def search_candidates(self, query, ctx, *, size, limit, exclude):
        out = []
        for i in range(min(limit, 2)):
            cid = f"clip-{i}"
            if cid in exclude:
                continue
            out.append(MediaCandidate(
                id=cid, thumb_url=f"poster{i}", full_url=f"https://v/{i}.mp4",
                title=cid, author="Cam", license="Pexels License (free, CC0-like)",
                media_type="video", duration=10.0, poster_url=f"poster{i}",
                video_url=f"https://v/{i}.mp4",
            ))
        return out


class _Episode:
    def __init__(self, segments):
        self.segments = segments


class _Seg:
    def __init__(self, index, query):
        self.index = index
        self.image_query = query
        self.image_label = f"{index:02d}_x"
        self.narration = "some narration"


class _Registry:
    def __init__(self, clients, by_id):
        self._clients = clients
        self._by_id = by_id

    def for_capability(self, task):
        return list(self._clients)

    def try_get(self, pid):
        return self._by_id.get(pid)


async def test_aggregate_web_grid_mixes_images_and_clips():
    photo, clip = _PhotoClient(), _ClipClient(available=True)
    reg = _Registry([photo, clip], {"web-commons": photo, "web-pexels": clip})
    ctx = _ctx()

    # web_media_providers("web") -> both (keyless first), ordered photo then clip.
    providers = await cur.web_media_providers(reg, "web", ctx)
    assert [c.provider_id for c in providers] == ["web-commons", "web-pexels"]

    ep = _Episode([_Seg(1, "ocean")])
    blob = await cur.build_curation(reg, providers, ep, ctx, size="16:9")
    assert blob["provider"] == "web"
    seg = blob["segments"][0]
    kinds = {c["media_type"] for c in seg["candidates"]}
    assert kinds == {"image", "video"}  # MIXED grid
    sources = {c["provider"] for c in seg["candidates"]}
    assert sources == {"web-commons", "web-pexels"}
    # Photos first -> default chosen is an image.
    assert seg["chosen_id"].startswith("img-")
    assert cur.chosen_provider_for(blob, 1) == "web-commons"
    assert cur.chosen_candidate_for(blob, 1).media_type == "image"


async def test_choosing_a_clip_resolves_video_provider():
    photo, clip = _PhotoClient(), _ClipClient(available=True)
    reg = _Registry([photo, clip], {"web-commons": photo, "web-pexels": clip})
    ctx = _ctx()
    providers = await cur.web_media_providers(reg, "web", ctx)
    ep = _Episode([_Seg(1, "ocean")])
    blob = await cur.build_curation(reg, providers, ep, ctx, size="16:9")

    updated, invalid = cur.apply_selection(blob, {1: "clip-0"})
    assert invalid == []
    chosen = cur.chosen_candidate_for(updated, 1)
    assert chosen.is_video and chosen.video_url.endswith("/0.mp4")
    assert cur.chosen_provider_for(updated, 1) == "web-pexels"


async def test_no_clip_provider_is_photos_only():
    photo, clip = _PhotoClient(), _ClipClient(available=False)  # no Pexels key
    reg = _Registry([photo, clip], {"web-commons": photo, "web-pexels": clip})
    ctx = _ctx()
    providers = await cur.web_media_providers(reg, "web", ctx)
    assert [c.provider_id for c in providers] == ["web-commons"]  # clip dropped
    ep = _Episode([_Seg(1, "ocean")])
    blob = await cur.build_curation(reg, providers, ep, ctx, size="16:9")
    seg = blob["segments"][0]
    assert {c["media_type"] for c in seg["candidates"]} == {"image"}  # photos only


async def test_single_web_pexels_provider_curation():
    clip = _ClipClient(available=True)
    reg = _Registry([clip], {"web-pexels": clip})
    ctx = _ctx()
    providers = await cur.web_media_providers(reg, "web-pexels", ctx)
    assert [c.provider_id for c in providers] == ["web-pexels"]
    assert cur.provider_supports_candidates(reg, "web-pexels") is True
    assert cur.provider_supports_candidates(reg, "web") is True
    ep = _Episode([_Seg(1, "ocean")])
    blob = await cur.build_curation(reg, providers, ep, ctx, size="16:9")
    assert blob["provider"] == "web-pexels"
    assert blob["segments"][0]["chosen_id"].startswith("clip-")


async def test_generative_provider_has_no_curation():
    reg = _Registry([], {})
    ctx = _ctx()
    assert cur.provider_supports_candidates(reg, "kie") is False
    assert await cur.web_media_providers(reg, "kie", ctx) == []


# --------------------------------------------------------------------------- #
# Runner — a chosen VIDEO clip routes to an mp4 path + media_type "video"      #
# --------------------------------------------------------------------------- #
async def test_runner_downloads_chosen_video_to_mp4(monkeypatch, tmp_path):
    import module2.runner as runner
    from clients.base import ImageResult
    from models.spec import (
        EpisodeSpec, ImageStyle, SegmentSpec, SeriesSpec, VoiceConfig,
    )
    from module2.materialize import layout_for

    downloaded: list[tuple[str, str]] = []  # (candidate_id, out suffix)

    class _PexelsClient:
        provider_id = "web-pexels"
        supports_candidates = True

        async def download_chosen(self, candidate, out_path, ctx):
            downloaded.append((candidate.id, Path(out_path).suffix))
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"\x00" * 30000)
            return ImageResult(
                out_path=Path(out_path), count=1,
                raw={"media_type": "video", "chosen_id": candidate.id,
                     "attribution": {"title": candidate.id, "author": "Cam",
                                     "license": "Pexels License (free, CC0-like)",
                                     "source_url": "u", "descriptionurl": "d"}},
            )

    class _CommonsClient:
        provider_id = "web-commons"
        supports_candidates = True

        async def generate_image(self, req, out_path, ctx):
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"\x00" * 30000)
            return ImageResult(out_path=Path(out_path), count=1, raw={})

    by_id = {"web-pexels": _PexelsClient(), "web-commons": _CommonsClient()}

    class _Reg:
        async def resolve(self, task, provider, ctx):
            return by_id["web-commons"]

        def try_get(self, pid):
            return by_id.get(pid)

    seg = SegmentSpec(index=1, narration="hi", image_prompt="ocean", image_label="01_ocean",
                      image_query="ocean")
    ep = EpisodeSpec(episode_id="e1", title="T", order=1, status="scripted", segments=[seg])
    spec = SeriesSpec(
        series_id="s1", name="S", topic="t", skill="explain", language="en",
        target_minutes=1, density="standard",
        providers={"script": "stub-script", "image": "web", "voice": "stub-voice"},
        image_style=ImageStyle(preset_id="c", base_prompt="b", aspect="16:9"),
        voice=VoiceConfig(provider="stub-voice", voice_id="v"), episodes=[ep],
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(runner, "update_job", _noop)

    lo = layout_for(tmp_path / "proj")
    for d in (lo.root, lo.images_dir, lo.voice_dir, lo.music_dir, lo.thumbnails_dir):
        d.mkdir(parents=True, exist_ok=True)
    lo.image_txt(1, "01_ocean").write_text("ocean", encoding="utf-8")

    curation = {
        "provider": "web",
        "segments": [
            {"index": 1, "query": "ocean",
             "candidates": [
                 {"id": "pexels-9", "thumb_url": "p", "full_url": "https://v/9.mp4",
                  "media_type": "video", "video_url": "https://v/9.mp4", "duration": 10.0,
                  "provider": "web-pexels"},
             ],
             "chosen_id": "pexels-9"},
        ],
    }

    ctx = _ctx()
    errors = await runner.run_images(
        spec, ep, lo, ctx, ["j1"], "u1", registry=_Reg(), concurrency=1, curation=curation,
    )
    assert errors == [None]
    # The clip was downloaded via the web-pexels client to an .mp4 path.
    assert downloaded == [("pexels-9", ".mp4")]
    plan = ctx.extra["media_plan"]
    assert plan[1]["media_type"] == "video"
    assert plan[1]["path"].suffix == ".mp4"
    # Credits captured the clip with media_type=video + Pexels license.
    credits = ctx.extra["commons_credits"]
    assert credits[0]["media_type"] == "video"
    assert "Pexels" in credits[0]["license"]
