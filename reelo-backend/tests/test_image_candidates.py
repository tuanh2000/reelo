"""Human image curation (M2-12) — candidate search + endpoints + runner.

Three offline layers (no network / DB / ffmpeg):

1. Unit — :meth:`CommonsImageClient.search_candidates` with a fake httpx client:
   license/raster filter, ``limit`` cap, ``exclude`` dedup, thumb_url +
   attribution; and :meth:`download_chosen` writes the chosen image + attribution.
2. Endpoints — ``GET /episodes/{id}/image-candidates`` (search→cache→re-read) and
   ``POST /episodes/{id}/image-selection`` (validate + apply), incl. the 409 for
   generative providers.
3. Runner — ``run_images`` downloads the user-chosen candidate (download_chosen)
   instead of auto-picking, and falls back to generate_image when no choice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import clients.commons_image as commons_image
from clients.base import (
    CallContext,
    ImageCandidate,
    ImageResult,
    ServiceConfig,
)
from clients.commons_image import CommonsImageClient
from keystore import Cipher, KeyStore
from usage import UsageLogger

# A 1x1 PNG padded past the client's _MIN_BYTES floor so the download is kept.
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc0f01f0005010102a0bb3e9c0000000049454e44ae426082"
)
_FAKE_IMG = _PNG_1x1 + b"\x00" * 6000


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, *, json_data=None, content=b"") -> None:
        self._json = json_data
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json


def _search_pages(pages: list[dict]) -> dict:
    return {"query": {"pages": {str(i): p for i, p in enumerate(pages)}}}


def _page(
    title: str,
    *,
    license_name: str,
    mime: str = "image/jpeg",
    index: int = 0,
    thumb: str = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/X.jpg/360px-X.jpg",
    author: str = "Jane Doe",
) -> dict:
    return {
        "title": title,
        "index": index,
        "imageinfo": [
            {
                "mime": mime,
                "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/X.jpg",
                "thumburl": thumb,
                "thumbwidth": 360,
                "thumbheight": 240,
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
                "extmetadata": {
                    "LicenseShortName": {"value": license_name},
                    "Artist": {"value": f"<a href='#'>{author}</a>"},
                },
            }
        ],
    }


class _FakeAsyncClient:
    def __init__(self, search_response: dict, **_kw) -> None:
        self._search_response = search_response
        self.search_calls: list[dict] = []
        self.download_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, params=None, timeout=None):
        if params and params.get("action") == "query":
            self.search_calls.append(params)
            return _FakeResponse(json_data=self._search_response)
        self.download_calls.append(url)
        return _FakeResponse(content=_FAKE_IMG)


def _install_fake(monkeypatch, pages: list[dict]) -> dict:
    holder: dict = {}
    response = _search_pages(pages)

    def _factory(**kw):
        inst = _FakeAsyncClient(response, **kw)
        holder["client"] = inst
        return inst

    monkeypatch.setattr(commons_image.httpx, "AsyncClient", _factory)
    return holder


def _cfg() -> ServiceConfig:
    return ServiceConfig(
        provider_id="web-commons",
        raw={
            "auth": {"type": "none"},
            "tasks": {"generate-image": {"sizes": ["16:9"], "default_size": "16:9"}},
            "pricing": {"generate-image": {"per_image": 0.0}},
            "retries": 0,
            "search_limit": 30,
        },
    )


def _ctx(extra: dict | None = None) -> CallContext:
    return CallContext(
        user_id="u1",
        keys=KeyStore(Cipher(b"k" * 32)),
        usage=UsageLogger(),
        extra=extra if extra is not None else {},
    )


# --------------------------------------------------------------------------- #
# 1. Unit — search_candidates                                                 #
# --------------------------------------------------------------------------- #
async def test_search_candidates_limits_filters_and_carries_attribution(monkeypatch):
    # 12 good CC-BY raster + 1 NC + 1 SVG. We expect 9 (limit), filtered.
    pages = [
        _page(f"File:Good{i}.jpg", license_name="CC BY 4.0", index=i, author=f"A{i}")
        for i in range(12)
    ]
    pages.append(_page("File:NC.jpg", license_name="CC BY-NC 4.0", index=12))
    pages.append(_page("File:Vec.svg", license_name="CC0", mime="image/svg+xml", index=13))
    holder = _install_fake(monkeypatch, pages)
    client = CommonsImageClient(_cfg())

    cands = await client.search_candidates("horseshoe crab beach", _ctx(), size="16:9", limit=9)

    assert len(cands) == 9  # limit honoured; NC + SVG excluded
    ids = {c.id for c in cands}
    assert "File:NC.jpg" not in ids and "File:Vec.svg" not in ids
    first = cands[0]
    assert first.thumb_url.endswith("360px-X.jpg")  # small preview
    assert first.license == "CC BY 4.0"
    assert first.author == "A0"  # HTML stripped
    assert first.source_url.endswith("File:Good0.jpg")
    # full_url is a re-sized large URL (1600px for 16:9), not the small thumb.
    assert "1600px-" in first.full_url
    # search ran at the SMALL thumb width (light grid), large fetched later.
    assert holder["client"].search_calls[0]["iiurlwidth"] == "360"
    # NOTHING downloaded during candidate search.
    assert holder["client"].download_calls == []


async def test_search_candidates_excludes_used_titles(monkeypatch):
    pages = [
        _page("File:A.jpg", license_name="CC0", index=0),
        _page("File:B.jpg", license_name="CC0", index=1),
    ]
    _install_fake(monkeypatch, pages)
    client = CommonsImageClient(_cfg())
    cands = await client.search_candidates("crab", _ctx(), exclude={"File:A.jpg"})
    assert [c.id for c in cands] == ["File:B.jpg"]


async def test_download_chosen_writes_image_and_attribution(monkeypatch, tmp_path: Path):
    holder = _install_fake(monkeypatch, [])  # download path doesn't search
    client = CommonsImageClient(_cfg())
    cand = ImageCandidate(
        id="File:Pick.jpg",
        thumb_url="https://upload/360px-Pick.jpg",
        full_url="https://upload/1600px-Pick.jpg",
        title="File:Pick.jpg",
        author="Ada L.",
        license="CC BY 4.0",
        source_url="https://commons/File:Pick.jpg",
    )
    out = tmp_path / "01_crab.png"
    res = await client.download_chosen(cand, out, _ctx())

    assert res.out_path.exists() and res.out_path.stat().st_size >= 5000
    # The LARGE url was fetched (the chosen image), not the preview.
    assert holder["client"].download_calls == ["https://upload/1600px-Pick.jpg"]
    attr = res.raw["attribution"]
    assert attr["license"] == "CC BY 4.0" and attr["author"] == "Ada L."
    assert res.raw["chosen_id"] == "File:Pick.jpg"


def test_supports_candidates_flag():
    assert CommonsImageClient(_cfg()).supports_candidates is True
    # The base default is False (AI providers have no selection step).
    from clients.base import AIClient

    assert AIClient.supports_candidates is False


# --------------------------------------------------------------------------- #
# 2. curation helper                                                          #
# --------------------------------------------------------------------------- #
async def test_build_curation_default_selects_first_and_dedups():
    from models.spec import EpisodeSpec, SegmentSpec
    from module2 import curation as cur

    class _Client:
        provider_id = "web-commons"
        supports_candidates = True

        async def search_candidates(self, query, ctx, *, size, limit, exclude):
            # Two hits; the first overlaps with the previous segment's pick.
            base = [
                ImageCandidate(id=f"{query}-A", thumb_url="t", full_url="f", title="A"),
                ImageCandidate(id=f"{query}-B", thumb_url="t", full_url="f", title="B"),
            ]
            return [c for c in base if c.id not in exclude]

    class _Reg:
        def try_get(self, pid):
            return _Client()

    ep = EpisodeSpec(
        episode_id="e1", title="T", order=1, status="scripted",
        segments=[
            SegmentSpec(index=1, narration="n1", image_prompt="p", image_label="01_a",
                        image_query="q"),
            SegmentSpec(index=2, narration="n2", image_prompt="p", image_label="02_b",
                        image_query="q"),
        ],
    )
    blob = await cur.build_curation(_Reg(), _Client(), ep, _ctx())
    assert blob["provider"] == "web-commons"
    segs = blob["segments"]
    # Each segment default-selects its first candidate.
    assert segs[0]["chosen_id"] == "q-A"
    # Segment 2 dedups q-A (used by seg 1) -> its first becomes q-B.
    assert segs[1]["chosen_id"] == "q-B"


def test_apply_selection_validates_membership():
    from module2 import curation as cur

    blob = {
        "provider": "web-commons",
        "segments": [
            {"index": 1, "query": "q", "candidates": [{"id": "a"}, {"id": "b"}], "chosen_id": "a"},
        ],
    }
    updated, invalid = cur.apply_selection(blob, {1: "b"})
    assert invalid == [] and updated["segments"][0]["chosen_id"] == "b"
    _, bad = cur.apply_selection(blob, {1: "zzz"})
    assert bad == [1]
    _, bad2 = cur.apply_selection(blob, {99: "a"})
    assert bad2 == [99]


def test_chosen_candidate_for_falls_back_to_first():
    from module2 import curation as cur

    blob = {
        "segments": [
            {"index": 1, "candidates": [{"id": "a", "thumb_url": "t", "full_url": "f"},
                                        {"id": "b", "thumb_url": "t", "full_url": "f"}],
             "chosen_id": "b"},
            {"index": 2, "candidates": [{"id": "c", "thumb_url": "t", "full_url": "f"}],
             "chosen_id": None},  # no choice -> first
        ],
    }
    assert cur.chosen_candidate_for(blob, 1).id == "b"
    assert cur.chosen_candidate_for(blob, 2).id == "c"  # falls back to first
    assert cur.chosen_candidate_for(blob, 3) is None  # missing segment
    assert cur.chosen_candidate_for(None, 1) is None


# --------------------------------------------------------------------------- #
# 3. Endpoints — GET caches, POST validates, AI -> 409                        #
# --------------------------------------------------------------------------- #
class _EpRow:
    def __init__(self, ep_id):
        self.id = ep_id
        self.image_curation = None


class _Store:
    def __init__(self):
        self.episodes: dict[str, _EpRow] = {}
        self.series: dict = {}
        self.search_count = 0


class _FakeEpisodeRepo:
    def __init__(self, store):
        self.store = store

    async def get(self, user_id, episode_id):
        return self.store.episodes.get(episode_id)

    async def get_curation(self, user_id, episode_id):
        ep = self.store.episodes.get(episode_id)
        return ep.image_curation if ep else None

    async def set_curation(self, user_id, episode_id, curation):
        ep = self.store.episodes.get(episode_id)
        if ep:
            ep.image_curation = curation
        return ep


@pytest.fixture()
def cur_client(monkeypatch):
    from fastapi.testclient import TestClient

    import web.routers.episodes as ep_router
    from models.spec import (
        EpisodeSpec,
        ImageStyle,
        SegmentSpec,
        SeriesSpec,
        VoiceConfig,
    )
    from web.app import create_app
    from web.deps import get_current_user, get_db

    store = _Store()
    store.episodes["e1"] = _EpRow("e1")

    def _spec(image_provider: str):
        return SeriesSpec(
            series_id="s1", name="S", topic="t", skill="religion", language="vi",
            target_minutes=1, density="standard",
            providers={"script": "stub-script", "image": image_provider, "voice": "stub-voice"},
            image_style=ImageStyle(preset_id="p", base_prompt="b", aspect="16:9"),
            voice=VoiceConfig(provider="stub-voice", voice_id="v"),
            episodes=[
                EpisodeSpec(
                    episode_id="e1", title="T", order=1, status="scripted",
                    segments=[
                        SegmentSpec(index=1, narration="word " * 5, image_prompt="p",
                                    image_label="01_a", image_query="crab"),
                        SegmentSpec(index=2, narration="word " * 5, image_prompt="p",
                                    image_label="02_b", image_query="bird"),
                    ],
                )
            ],
        )

    state = {"image_provider": "web-commons"}

    async def _find(session, user_id, episode_id):
        if episode_id != "e1":
            return None
        sp = _spec(state["image_provider"])
        return object(), sp, sp.episodes[0]

    class _Client:
        provider_id = "web-commons"
        supports_candidates = True
        requires_key = False

        async def is_available(self, ctx):
            return True

        async def search_candidates(self, query, ctx, *, size, limit, exclude):
            store.search_count += 1
            return [
                ImageCandidate(id=f"{query}-{i}", thumb_url=f"t{i}", full_url=f"f{i}",
                               title=f"{query}-{i}", author="A", license="CC0")
                for i in range(3)
            ]

    class _Reg:
        def try_get(self, pid):
            return _Client() if pid == "web-commons" else None

        def get(self, pid):
            return _Client()

        def for_capability(self, task):
            return [_Client()]

    monkeypatch.setattr(ep_router, "find_series_for_episode", _find)
    monkeypatch.setattr(ep_router, "EpisodeRepo", lambda s: _FakeEpisodeRepo(store))
    monkeypatch.setattr(ep_router, "get_registry", lambda: _Reg())
    # candidate provider check uses cur.provider_supports_candidates(registry,...)
    monkeypatch.setattr(
        ep_router.cur, "provider_supports_candidates",
        lambda reg, pid: pid == "web-commons",
    )
    # The endpoint preloads the user's keys for web-pexels availability; in this
    # offline test there is no DB, so stub the keystore loader to an empty store.
    async def _fake_keystore(repo, cipher, user_id):
        return KeyStore(Cipher(b"k" * 32))

    monkeypatch.setattr(ep_router, "load_user_keystore", _fake_keystore)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: "u1"

    class _FakeDb:
        async def flush(self):
            return None

    async def _fake_db():
        yield _FakeDb()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.store = store  # type: ignore[attr-defined]
    client.state = state  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def test_get_candidates_searches_then_caches(cur_client):
    r1 = cur_client.get("/episodes/e1/image-candidates")
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["provider"] == "web-commons"
    assert len(body["segments"]) == 2
    seg1 = body["segments"][0]
    assert len(seg1["candidates"]) == 3
    assert seg1["chosen_id"] == seg1["candidates"][0]["id"]  # first default-selected
    assert seg1["candidates"][0]["thumb_url"] == "t0"
    count_after_first = cur_client.store.search_count
    assert count_after_first == 2  # one search per segment

    # Second call returns the cache; no new searches.
    r2 = cur_client.get("/episodes/e1/image-candidates")
    assert r2.status_code == 200
    assert cur_client.store.search_count == count_after_first


def test_get_candidates_409_for_ai_provider(cur_client):
    cur_client.state["image_provider"] = "kie"
    r = cur_client.get("/episodes/e1/image-candidates")
    assert r.status_code == 409
    assert "automatic" in r.json()["detail"]


def test_get_candidates_404_missing_episode(cur_client):
    assert cur_client.get("/episodes/nope/image-candidates").status_code == 404


def test_post_selection_validates_and_applies(cur_client):
    cur_client.get("/episodes/e1/image-candidates")  # seed cache
    # Choose the 2nd candidate of segment 1.
    r = cur_client.post("/episodes/e1/image-selection", json={"selections": {"1": "crab-1"}})
    assert r.status_code == 200, r.text
    seg1 = next(s for s in r.json()["segments"] if s["index"] == 1)
    assert seg1["chosen_id"] == "crab-1"


def test_post_selection_400_invalid_candidate(cur_client):
    cur_client.get("/episodes/e1/image-candidates")
    r = cur_client.post("/episodes/e1/image-selection", json={"selections": {"1": "nope"}})
    assert r.status_code == 400


def test_post_selection_409_without_cache(cur_client):
    # No GET first -> no cache.
    r = cur_client.post("/episodes/e1/image-selection", json={"selections": {"1": "x"}})
    assert r.status_code == 409


def test_candidates_requires_auth():
    from fastapi.testclient import TestClient

    from web.app import create_app

    anon = TestClient(create_app())
    assert anon.get("/episodes/e1/image-candidates").status_code == 401


# --------------------------------------------------------------------------- #
# 4. Runner — uses chosen candidate (download_chosen) over auto generate      #
# --------------------------------------------------------------------------- #
async def test_runner_downloads_chosen_candidate(monkeypatch, tmp_path: Path):
    import module2.runner as runner
    from models.spec import EpisodeSpec, ImageStyle, SegmentSpec, SeriesSpec, VoiceConfig
    from module2.materialize import layout_for

    downloaded: list[str] = []
    auto_called: list[str] = []

    class _FakeClient:
        provider_id = "web-commons"
        supports_candidates = True

        async def download_chosen(self, candidate, out_path, ctx):
            downloaded.append(candidate.id)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(_FAKE_IMG)
            return ImageResult(
                out_path=Path(out_path), count=1,
                raw={"chosen_id": candidate.id,
                     "attribution": {"title": candidate.id, "author": "A",
                                     "license": "CC0", "source_url": "u",
                                     "descriptionurl": "d"}},
            )

        async def generate_image(self, req, out_path, ctx):  # pragma: no cover - fallback
            auto_called.append(req.label)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(_FAKE_IMG)
            return ImageResult(out_path=Path(out_path), count=1, raw={})

    fake_client = _FakeClient()

    class _FakeReg:
        async def resolve(self, task, provider, ctx):
            return fake_client

        def try_get(self, pid):
            return fake_client if pid == "web-commons" else None

    seg1 = SegmentSpec(index=1, narration="hi", image_prompt="a crab", image_label="01_crab",
                       image_query="crab")
    seg2 = SegmentSpec(index=2, narration="ho", image_prompt="a bird", image_label="02_bird",
                       image_query="bird")
    ep = EpisodeSpec(episode_id="e1", title="T", order=1, status="scripted", segments=[seg1, seg2])
    spec = SeriesSpec(
        series_id="s1", name="S", topic="t", skill="religion", language="vi",
        target_minutes=1, density="standard",
        providers={"script": "stub-script", "image": "web-commons", "voice": "stub-voice"},
        image_style=ImageStyle(preset_id="c", base_prompt="oil", aspect="16:9"),
        voice=VoiceConfig(provider="stub-voice", voice_id="v"), episodes=[ep],
    )

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(runner, "update_job", _noop)

    lo = layout_for(tmp_path / "proj")
    for d in (lo.root, lo.images_dir, lo.voice_dir, lo.music_dir, lo.thumbnails_dir):
        d.mkdir(parents=True, exist_ok=True)
    lo.image_txt(1, "01_crab").write_text("a crab", encoding="utf-8")
    lo.image_txt(2, "02_bird").write_text("a bird", encoding="utf-8")

    # Curation: seg 1 has a chosen candidate; seg 2 has none -> auto fallback.
    curation = {
        "provider": "web-commons",
        "segments": [
            {"index": 1, "query": "crab",
             "candidates": [{"id": "File:Crab.jpg", "thumb_url": "t", "full_url": "f"}],
             "chosen_id": "File:Crab.jpg"},
            {"index": 2, "query": "bird", "candidates": [], "chosen_id": None},
        ],
    }

    ctx = _ctx()
    errors = await runner.run_images(
        spec, ep, lo, ctx, ["j1", "j2"], "u1", registry=_FakeReg(), concurrency=1,
        curation=curation,
    )
    assert errors == [None, None]
    assert downloaded == ["File:Crab.jpg"]  # chosen path used for seg 1
    assert auto_called == ["02_bird"]  # seg 2 (no choice) fell back to auto
