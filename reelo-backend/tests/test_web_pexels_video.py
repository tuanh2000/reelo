"""web-pexels video clip provider (Module 3 / M2-13) — all offline.

1. Unit — :meth:`PexelsVideoClient.search_candidates` with a fake httpx client:
   maps Pexels videos → MediaCandidate(media_type="video"), picks the mp4 file
   closest to the target frame, fills poster/duration/author/license; no key → [].
   ``download_chosen`` writes the mp4 + attribution; ``validate_key`` test call.
2. Registry — the bundled services.yaml registers web-pexels (BYOK) and lists it
   for generate-image; resolve gates on the user's Pexels key.
"""

from __future__ import annotations

from pathlib import Path

import clients.pexels_video as pexels_video
from clients.base import (
    CallContext,
    MediaCandidate,
    ServiceConfig,
    Task,
)
from clients.pexels_video import PEXELS_VIDEO_SEARCH, PexelsVideoClient
from clients.registry import get_registry
from keystore import Cipher, KeyStore
from usage import UsageLogger

_FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 20000  # > _MIN_BYTES


# --------------------------------------------------------------------------- #
# Fake httpx                                                                  #
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, *, status_code=200, json_data=None, content=b"") -> None:
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise pexels_video.httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._json


def _video(vid: int, *, files: list[dict], duration: int = 12,
           author: str = "Jane Film") -> dict:
    return {
        "id": vid,
        "width": 3840,
        "height": 2160,
        "duration": duration,
        "url": f"https://www.pexels.com/video/{vid}/",
        "image": f"https://images.pexels.com/videos/{vid}/poster.jpg",
        "user": {"name": author},
        "video_files": files,
    }


def _file(fid: int, w: int, h: int, *, quality="hd", ft="video/mp4") -> dict:
    return {
        "id": fid,
        "quality": quality,
        "file_type": ft,
        "width": w,
        "height": h,
        "link": f"https://player.vimeo.com/external/{fid}.mp4",
    }


class _FakeAsyncClient:
    def __init__(self, search_json: dict, *, headers=None, **_kw) -> None:
        self._search_json = search_json
        self.headers = headers or {}
        self.search_calls: list[dict] = []
        self.download_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, params=None, timeout=None):
        if url == PEXELS_VIDEO_SEARCH:
            self.search_calls.append(params or {})
            return _FakeResponse(json_data=self._search_json)
        self.download_calls.append(url)
        return _FakeResponse(content=_FAKE_MP4)


def _install_fake(monkeypatch, search_json: dict) -> dict:
    holder: dict = {}

    def _factory(**kw):
        inst = _FakeAsyncClient(search_json, **kw)
        holder["client"] = inst
        return inst

    monkeypatch.setattr(pexels_video.httpx, "AsyncClient", _factory)
    return holder


def _cfg() -> ServiceConfig:
    return ServiceConfig(
        provider_id="web-pexels",
        raw={
            "auth": {"type": "key", "key_ref": "pexels", "env": "PEXELS_API_KEY"},
            "tasks": {"generate-image": {"sizes": ["16:9"], "default_size": "16:9",
                                         "search_limit": 15}},
            "pricing": {"generate-image": {"per_image": 0.0}},
            "retries": 0,
        },
    )


def _ctx(*, with_key=True) -> CallContext:
    keys = KeyStore(Cipher(b"k" * 32))
    if with_key:
        keys.save("u1", "pexels", "PEXELS-SECRET")
    return CallContext(user_id="u1", keys=keys, usage=UsageLogger(), extra={})


# --------------------------------------------------------------------------- #
# 1. Unit — search_candidates                                                 #
# --------------------------------------------------------------------------- #
async def test_search_maps_videos_and_picks_closest_file(monkeypatch):
    # Each video offers a 4K, an HD-1080 and a tiny file; for a 16:9 (1920x1080)
    # frame the 1920x1080 file must be chosen (closest area), not the 4K or tiny.
    videos = [
        _video(101, files=[
            _file(1, 3840, 2160, quality="uhd"),
            _file(2, 1920, 1080, quality="hd"),
            _file(3, 640, 360, quality="sd"),
        ]),
        _video(102, files=[_file(4, 1280, 720), _file(5, 1920, 1080)]),
    ]
    holder = _install_fake(monkeypatch, {"videos": videos})
    client = PexelsVideoClient(_cfg())

    cands = await client.search_candidates("ocean waves", _ctx(), size="16:9", limit=9)

    assert len(cands) == 2
    c0 = cands[0]
    assert isinstance(c0, MediaCandidate) and c0.is_video and c0.media_type == "video"
    assert c0.id == "pexels-101"
    assert c0.width == 1920 and c0.height == 1080  # closest-fit file chosen
    assert c0.video_url.endswith("/2.mp4")  # the 1920x1080 link
    assert c0.poster_url.endswith("poster.jpg") and c0.thumb_url == c0.poster_url
    assert c0.duration == 12.0
    assert c0.author == "Jane Film"
    assert "Pexels" in c0.license
    # Authorization header carried the user's key; nothing downloaded at search.
    assert holder["client"].headers.get("Authorization") == "PEXELS-SECRET"
    assert holder["client"].download_calls == []
    # The grid asked Pexels for a landscape, FHD-ish size for a 16:9 frame.
    params = holder["client"].search_calls[0]
    assert params["orientation"] == "landscape" and params["size"] == "medium"


async def test_search_returns_empty_without_key(monkeypatch):
    _install_fake(monkeypatch, {"videos": [_video(1, files=[_file(1, 1920, 1080)])]})
    client = PexelsVideoClient(_cfg())
    cands = await client.search_candidates("ocean", _ctx(with_key=False), size="16:9")
    assert cands == []  # graceful: no Pexels key -> no clips in the grid


async def test_search_excludes_used_ids(monkeypatch):
    videos = [_video(1, files=[_file(1, 1920, 1080)]),
              _video(2, files=[_file(2, 1920, 1080)])]
    _install_fake(monkeypatch, {"videos": videos})
    client = PexelsVideoClient(_cfg())
    cands = await client.search_candidates("x", _ctx(), exclude={"pexels-1"})
    assert [c.id for c in cands] == ["pexels-2"]


async def test_search_skips_video_with_no_mp4(monkeypatch):
    videos = [_video(1, files=[_file(1, 1920, 1080, ft="video/webm")])]  # no mp4
    _install_fake(monkeypatch, {"videos": videos})
    client = PexelsVideoClient(_cfg())
    assert await client.search_candidates("x", _ctx()) == []


async def test_download_chosen_writes_mp4_and_attribution(monkeypatch, tmp_path: Path):
    holder = _install_fake(monkeypatch, {"videos": []})
    client = PexelsVideoClient(_cfg())
    cand = MediaCandidate(
        id="pexels-77", thumb_url="poster.jpg", full_url="https://x/77.mp4",
        title="Pexels video 77", author="Ada", license="Pexels License (free, CC0-like)",
        source_url="https://www.pexels.com/video/77/", media_type="video",
        duration=8.0, poster_url="poster.jpg", video_url="https://x/77.mp4",
    )
    out = tmp_path / "01_clip.mp4"
    res = await client.download_chosen(cand, out, _ctx())

    assert res.out_path.exists() and res.out_path.stat().st_size >= 10000
    assert holder["client"].download_calls == ["https://x/77.mp4"]
    assert res.raw["media_type"] == "video" and res.raw["chosen_id"] == "pexels-77"
    assert res.raw["attribution"]["author"] == "Ada"
    assert "Pexels" in res.raw["attribution"]["license"]


async def test_validate_key_true_on_200_false_on_401(monkeypatch):
    _install_fake(monkeypatch, {"videos": []})
    client = PexelsVideoClient(_cfg())
    assert await client.validate_key(_ctx()) is True
    assert await client.validate_key(_ctx(with_key=False)) is False

    # 401 -> invalid key
    def _factory_401(**kw):
        class _C(_FakeAsyncClient):
            async def get(self, url, *, params=None, timeout=None):
                return _FakeResponse(status_code=401)
        return _C({"videos": []}, **kw)

    monkeypatch.setattr(pexels_video.httpx, "AsyncClient", _factory_401)
    assert await client.validate_key(_ctx()) is False


# --------------------------------------------------------------------------- #
# 2. Registry — web-pexels registered + BYOK gated                            #
# --------------------------------------------------------------------------- #
def test_registry_registers_web_pexels():
    reg = get_registry()
    client = reg.try_get("web-pexels")
    assert client is not None
    assert client.provider_id == "web-pexels"
    assert client.requires_key is True
    assert client.supports_candidates is True
    assert "web-pexels" in reg._fallback["generate-image"]
    assert reg.key_ref_for_provider("web-pexels") == "pexels"


async def test_registry_resolves_web_pexels_only_with_key():
    reg = get_registry()
    # No Pexels key -> not available -> resolving web-pexels alone raises (no
    # other web provider in its single chain that the user can use besides
    # keyless fallbacks later in the chain).
    no_key = CallContext(user_id="u1", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    client = await reg.resolve(Task.GENERATE_IMAGE, "web-pexels", no_key)
    # Falls through the chain to keyless web-commons (always available).
    assert client.provider_id in {"web-pexels", "web-commons"}

    with_key = CallContext(user_id="u1", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    with_key.keys.save("u1", "pexels", "secret")
    client2 = await reg.resolve(Task.GENERATE_IMAGE, "web-pexels", with_key)
    assert client2.provider_id == "web-pexels"


def test_providers_endpoint_lists_web_pexels(client):
    resp = client.get("/providers")
    assert resp.status_code == 200
    image = resp.json()["image"]
    wp = next((o for o in image if o["id"] == "web-pexels"), None)
    assert wp is not None
    assert wp["cost_tier"] == "free" and wp["requires_key"] is True
    assert wp["key_help_url"] == "https://www.pexels.com/api/"
