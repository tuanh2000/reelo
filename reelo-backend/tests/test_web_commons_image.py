"""web-commons real-photo image provider (Module 3 / M2-11).

Three layers, all offline:

1. Unit — :class:`CommonsImageClient` with a fake httpx ``AsyncClient`` (no
   network): license filter rejects CC-BY-NC and accepts PD/CC0/CC-BY, the file
   is downloaded, and attribution is returned in ``ImageResult.raw``.
2. Registry — ``registry.resolve(GENERATE_IMAGE, "web-commons", ctx)`` returns
   the keyless client with no user key configured.
3. Pipeline seams — Module 1 (stub-script) maps ``image_query`` onto the
   segment, and the Module 2 runner passes ``query``/``label`` down to the image
   client + persists attribution to ``credits.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import clients.commons_image as commons_image
from clients.base import CallContext, ImageRequest, ProviderUnavailableError, ServiceConfig, Task
from clients.commons_image import CommonsImageClient
from clients.registry import get_registry
from keystore import Cipher, KeyStore
from usage import UsageLogger

# A 1x1 PNG, padded past the client's _MIN_BYTES floor so the download is kept.
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc0f01f0005010102a0bb3e9c0000000049454e44ae426082"
)
_FAKE_IMG = _PNG_1x1 + b"\x00" * 6000  # > _MIN_BYTES (5000)


# --------------------------------------------------------------------------- #
# Fake httpx.AsyncClient                                                      #
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
    """Wrap a list of page dicts in the Commons query response envelope."""
    return {"query": {"pages": {str(i): p for i, p in enumerate(pages)}}}


def _page(title: str, *, license_name: str, mime: str = "image/jpeg", index: int = 0,
          url: str = "https://upload.wikimedia.org/x.jpg", author: str = "Jane Doe") -> dict:
    return {
        "title": title,
        "index": index,
        "imageinfo": [
            {
                "mime": mime,
                "url": url,
                "thumburl": url,
                "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}",
                "extmetadata": {
                    "LicenseShortName": {"value": license_name},
                    "Artist": {"value": f"<a href='#'>{author}</a>"},
                },
            }
        ],
    }


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` driven by a scripted search response."""

    def __init__(self, search_response: dict, **_kw) -> None:
        self._search_response = search_response
        self.search_calls: list[str] = []
        self.download_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, params=None, timeout=None):
        if params and params.get("action") == "query":
            self.search_calls.append(params.get("gsrsearch", ""))
            return _FakeResponse(json_data=self._search_response)
        # download
        self.download_calls.append(url)
        return _FakeResponse(content=_FAKE_IMG)


def _install_fake(monkeypatch, pages: list[dict]) -> dict:
    """Patch the client's AsyncClient to a fake; return a holder for the instance."""
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
# 1. Unit — license filter + download + attribution                          #
# --------------------------------------------------------------------------- #
async def test_skips_cc_by_nc_picks_cc_by(monkeypatch, tmp_path: Path):
    # First (most relevant) result is NC -> rejected; the CC-BY one is chosen.
    pages = [
        _page("File:NC.jpg", license_name="CC BY-NC 4.0", index=0),
        _page("File:Good.jpg", license_name="CC BY 4.0", index=1, author="Ada L."),
    ]
    holder = _install_fake(monkeypatch, pages)
    client = CommonsImageClient(_cfg())
    ctx = _ctx()
    out = tmp_path / "01_crab.png"

    res = await client.generate_image(
        ImageRequest(query="horseshoe crab beach", label="01_crab", size="16:9"), out, ctx
    )

    assert res.out_path.exists() and res.out_path.stat().st_size >= 5000
    attribution = res.raw["attribution"]
    assert attribution["license"] == "CC BY 4.0"
    assert attribution["author"] == "Ada L."  # HTML tags stripped
    assert attribution["title"] == "File:Good.jpg"
    assert attribution["source_url"].endswith("File:Good.jpg")
    # the NC one was searched but never downloaded
    assert holder["client"].download_calls == [pages[1]["imageinfo"][0]["url"]]
    # usage recorded (1 image, free)
    ev = ctx.usage._sink.events[0]  # type: ignore[attr-defined]
    assert ev.provider == "web-commons" and ev.units == 1.0


async def test_accepts_public_domain_and_cc0(monkeypatch, tmp_path: Path):
    for lic in ("Public domain", "CC0 1.0"):
        _install_fake(monkeypatch, [_page("File:X.jpg", license_name=lic)])
        client = CommonsImageClient(_cfg())
        res = await client.generate_image(
            ImageRequest(query="vaccine vial", size="16:9"), tmp_path / f"{lic}.png", _ctx()
        )
        assert res.raw["attribution"]["license"] == lic


async def test_rejects_non_raster_mime(monkeypatch, tmp_path: Path):
    # An SVG with a permissive license is still rejected (not raster).
    _install_fake(
        monkeypatch, [_page("File:Vector.svg", license_name="CC0", mime="image/svg+xml")]
    )
    client = CommonsImageClient(_cfg())
    with pytest.raises(ProviderUnavailableError):
        await client.generate_image(ImageRequest(query="map europe"), tmp_path / "x.png", _ctx())


async def test_raises_when_no_valid_image(monkeypatch, tmp_path: Path):
    # Only NC results -> nothing passes the license filter after all fallbacks.
    _install_fake(monkeypatch, [_page("File:NC.jpg", license_name="CC BY-NC-SA 4.0")])
    client = CommonsImageClient(_cfg())
    with pytest.raises(ProviderUnavailableError):
        await client.generate_image(
            ImageRequest(query="rare obscure thing"), tmp_path / "x.png", _ctx()
        )


async def test_dedup_avoids_same_title_within_episode(monkeypatch, tmp_path: Path):
    # Same single result for two segments; the second must NOT reuse it.
    _install_fake(monkeypatch, [_page("File:Only.jpg", license_name="CC0")])
    client = CommonsImageClient(_cfg())
    used: set[str] = set()
    ctx = _ctx(extra={"commons_used": used})

    await client.generate_image(ImageRequest(query="crab"), tmp_path / "1.png", ctx)
    assert "File:Only.jpg" in used
    # Second call: the only candidate is already used -> no valid image left.
    with pytest.raises(ProviderUnavailableError):
        await client.generate_image(ImageRequest(query="crab"), tmp_path / "2.png", ctx)


async def test_query_priority_uses_image_query_first(monkeypatch, tmp_path: Path):
    holder = _install_fake(monkeypatch, [_page("File:X.jpg", license_name="CC0")])
    client = CommonsImageClient(_cfg())
    await client.generate_image(
        ImageRequest(query="red knot bird flock", label="06_generic", size="16:9"),
        tmp_path / "06_generic.png",
        _ctx(),
    )
    # the explicit image_query is searched first
    assert holder["client"].search_calls[0] == "red knot bird flock"


async def test_query_fallback_to_deslugged_label(monkeypatch, tmp_path: Path):
    holder = _install_fake(monkeypatch, [_page("File:X.jpg", license_name="CC0")])
    client = CommonsImageClient(_cfg())
    await client.generate_image(
        ImageRequest(label="06_red_knot_bird", size="16:9"),  # no query
        tmp_path / "06_red_knot_bird.png",
        _ctx(),
    )
    assert holder["client"].search_calls[0] == "red knot bird"


# --------------------------------------------------------------------------- #
# 2. Registry — resolve web-commons keyless                                   #
# --------------------------------------------------------------------------- #
async def test_registry_resolves_web_commons_without_key():
    reg = get_registry()
    ctx = _ctx()  # no keys at all
    client = await reg.resolve(Task.GENERATE_IMAGE, "web-commons", ctx)
    assert client.provider_id == "web-commons"
    assert client.requires_key is False
    assert "web-commons" in reg._fallback["generate-image"]


def test_providers_endpoint_lists_web_commons(client):
    resp = client.get("/providers")
    assert resp.status_code == 200
    image = resp.json()["image"]
    wc = next((o for o in image if o["id"] == "web-commons"), None)
    assert wc is not None
    assert wc["cost_tier"] == "free" and wc["requires_key"] is False


# --------------------------------------------------------------------------- #
# 3. Pipeline seams — Module 1 maps image_query, Module 2 passes query down   #
# --------------------------------------------------------------------------- #
async def test_module1_stub_maps_image_query():
    """Module 1 lazy-script via stub-script populates SegmentSpec.image_query."""
    from models.spec import ImageStyle, VoiceConfig
    from module1.episode_script import generate_episode_script
    from module1.wizard import build_series_spec

    spec = build_series_spec(
        name="Faiths", topic="religion",
        outline=[{"id": "w1", "title": "Origins", "desc": "how it began", "pick": True}],
        skill="religion", language="vi", target_minutes=1, density="standard",
        providers={"script": "stub-script", "image": "web-commons", "voice": "stub-voice"},
        voice=VoiceConfig(provider="stub-voice", voice_id="v"),
        image_style=ImageStyle(preset_id="cinematic", base_prompt="oil", aspect="16:9"),
    )
    scripted = await generate_episode_script(spec, spec.episodes[0], _ctx(), registry=get_registry())
    assert scripted.segments
    # stub emits "placeholder scene N" for image_query; it survives parse+reindex.
    assert all(s.image_query for s in scripted.segments)


async def test_runner_passes_query_and_writes_credits(monkeypatch, tmp_path: Path):
    """run_images sends query/label to the client and credits are collected."""
    import module2.runner as runner
    from models.spec import EpisodeSpec, ImageStyle, SegmentSpec, SeriesSpec, VoiceConfig
    from module2.materialize import layout_for

    captured: list[ImageRequest] = []

    class _FakeClient:
        provider_id = "web-commons"

        async def generate_image(self, req, out_path, ctx):
            captured.append(req)
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(_FAKE_IMG)
            from clients.base import ImageResult
            return ImageResult(
                out_path=Path(out_path), count=1,
                raw={"attribution": {"title": f"File:{req.query}.jpg", "author": "A",
                                     "license": "CC0", "source_url": "u", "descriptionurl": "d"}},
            )

    class _FakeReg:
        async def resolve(self, task, provider, ctx):
            return _FakeClient()

    seg = SegmentSpec(index=1, narration="hi", image_prompt="a crab", image_label="01_crab",
                      image_query="horseshoe crab beach")
    ep = EpisodeSpec(episode_id="e1", title="T", order=1, status="scripted", segments=[seg])
    spec = SeriesSpec(
        series_id="s1", name="S", topic="t", skill="religion", language="vi",
        target_minutes=1, density="standard",
        providers={"script": "stub-script", "image": "web-commons", "voice": "stub-voice"},
        image_style=ImageStyle(preset_id="cinematic", base_prompt="oil", aspect="16:9"),
        voice=VoiceConfig(provider="stub-voice", voice_id="v"), episodes=[ep],
    )

    # Silence the per-job DB updates.
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(runner, "update_job", _noop)

    lo = layout_for(tmp_path / "proj")
    for d in (lo.root, lo.images_dir, lo.voice_dir, lo.music_dir, lo.thumbnails_dir):
        d.mkdir(parents=True, exist_ok=True)
    lo.image_txt(1, "01_crab").write_text("a crab", encoding="utf-8")

    ctx = _ctx()
    errors = await runner.run_images(
        spec, ep, lo, ctx, ["job1"], "u1", registry=_FakeReg(), concurrency=1
    )
    assert errors == [None]
    assert captured[0].query == "horseshoe crab beach"
    assert captured[0].label == "01_crab"

    # Attribution gathered in ctx.extra -> credits.json written by _write_credits.
    runner._write_credits(lo, ctx)
    credits = json.loads((lo.root / "credits.json").read_text(encoding="utf-8"))
    assert credits["images"][0]["license"] == "CC0"
    assert credits["images"][0]["title"] == "File:horseshoe crab beach.jpg"
