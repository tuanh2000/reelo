"""web-openverse real-photo image provider (Module 3 / web-* family).

All offline (a fake httpx ``AsyncClient`` — no network):

1. Unit — :class:`OpenverseImageClient`:
   - ``generate_image`` (auto path): license filter rejects NC/ND, accepts
     CC0/CC-BY, downloads the original, returns attribution in ``ImageResult.raw``,
     query priority + per-episode dedup.
   - ``search_candidates`` (curation): parses results into MediaCandidate, honours
     ``limit`` + ``exclude``, downloads nothing.
   - ``download_chosen`` writes the chosen image + attribution.
   - empty result → empty list (search) / ProviderUnavailableError (auto).
   - HTTP error → empty list (search) / ProviderUnavailableError (auto).
2. Registry — ``resolve(GENERATE_IMAGE, "web-openverse", ctx)`` returns the keyless
   client with no key configured; it is in the generate-image fallback chain.
3. ``GET /providers`` lists web-openverse (free, keyless).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import clients.openverse_image as openverse_image
from clients.base import (
    CallContext,
    ImageCandidate,
    ImageRequest,
    ProviderUnavailableError,
    ServiceConfig,
    Task,
)
from clients.openverse_image import OpenverseImageClient
from clients.registry import get_registry
from keystore import Cipher, KeyStore
from usage import UsageLogger

# A 1x1 PNG padded past the client's _MIN_BYTES floor so the download is kept.
_PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "53de0000000c4944415408d763f8cfc0f01f0005010102a0bb3e9c0000000049454e44ae426082"
)
_FAKE_IMG = _PNG_1x1 + b"\x00" * 6000  # > _MIN_BYTES (5000)


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


def _results(items: list[dict]) -> dict:
    """Wrap a list of result dicts in the Openverse search response envelope."""
    return {"result_count": len(items), "results": items}


def _item(
    rid: str,
    *,
    license_name: str = "by",
    title: str = "A nice photo",
    creator: str = "Jane Doe",
    url: str = "https://example.org/full.jpg",
    thumbnail: str = "https://example.org/thumb.jpg",
) -> dict:
    return {
        "id": rid,
        "title": title,
        "creator": creator,
        "license": license_name,
        "license_url": f"https://creativecommons.org/licenses/{license_name}/4.0/",
        "url": url,
        "thumbnail": thumbnail,
        "foreign_landing_url": f"https://source.example/{rid}",
        "source": "flickr",
        "width": 1600,
        "height": 1067,
    }


class _FakeAsyncClient:
    """Stand-in for ``httpx.AsyncClient`` driven by a scripted search response.

    If ``raise_on_search`` is set, the search GET raises that httpx error (to
    exercise the degrade-to-empty path).
    """

    def __init__(self, search_response: dict, *, raise_on_search: Exception | None = None, **_kw):
        self._search_response = search_response
        self._raise_on_search = raise_on_search
        self.search_calls: list[dict] = []
        self.download_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, *, params=None, timeout=None):
        if params and "q" in params:
            self.search_calls.append(params)
            if self._raise_on_search is not None:
                raise self._raise_on_search
            return _FakeResponse(json_data=self._search_response)
        # download
        self.download_calls.append(url)
        return _FakeResponse(content=_FAKE_IMG)


def _install_fake(monkeypatch, items: list[dict], *, raise_on_search=None) -> dict:
    holder: dict = {}
    response = _results(items)

    def _factory(**kw):
        inst = _FakeAsyncClient(response, raise_on_search=raise_on_search, **kw)
        holder["client"] = inst
        return inst

    monkeypatch.setattr(openverse_image.httpx, "AsyncClient", _factory)
    return holder


def _cfg() -> ServiceConfig:
    return ServiceConfig(
        provider_id="web-openverse",
        raw={
            "auth": {"type": "none"},
            "tasks": {
                "generate-image": {"sizes": ["16:9"], "default_size": "16:9", "search_limit": 9}
            },
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
# 1. Unit — auto path: license filter + download + attribution               #
# --------------------------------------------------------------------------- #
async def test_auto_skips_nd_picks_cc_by(monkeypatch, tmp_path: Path):
    # First result is ND -> rejected; the CC-BY one is chosen.
    items = [
        _item("nd-1", license_name="nd", title="No derivs"),
        _item("good-1", license_name="by", title="Horseshoe crab", creator="Ada L."),
    ]
    holder = _install_fake(monkeypatch, items)
    client = OpenverseImageClient(_cfg())
    ctx = _ctx()
    out = tmp_path / "01_crab.png"

    res = await client.generate_image(
        ImageRequest(query="horseshoe crab beach", label="01_crab", size="16:9"), out, ctx
    )

    assert res.out_path.exists() and res.out_path.stat().st_size >= 5000
    attr = res.raw["attribution"]
    assert attr["license"] == "by"
    assert attr["author"] == "Ada L."
    assert attr["title"] == "Horseshoe crab"
    assert attr["source_url"].endswith("good-1")
    # the ND one was never downloaded; only the chosen original url was fetched
    assert holder["client"].download_calls == [items[1]["url"]]
    ev = ctx.usage._sink.events[0]  # type: ignore[attr-defined]
    assert ev.provider == "web-openverse" and ev.units == 1.0


async def test_auto_accepts_cc0_and_pdm(monkeypatch, tmp_path: Path):
    for lic in ("cc0", "pdm"):
        _install_fake(monkeypatch, [_item("x", license_name=lic)])
        client = OpenverseImageClient(_cfg())
        res = await client.generate_image(
            ImageRequest(query="vaccine vial", size="16:9"), tmp_path / f"{lic}.png", _ctx()
        )
        assert res.raw["attribution"]["license"] == lic


async def test_auto_raises_when_no_valid_image(monkeypatch, tmp_path: Path):
    # Only NC results -> nothing passes the license filter.
    _install_fake(monkeypatch, [_item("nc-1", license_name="by-nc")])
    client = OpenverseImageClient(_cfg())
    with pytest.raises(ProviderUnavailableError):
        await client.generate_image(
            ImageRequest(query="rare obscure thing"), tmp_path / "x.png", _ctx()
        )


async def test_auto_raises_on_empty_results(monkeypatch, tmp_path: Path):
    _install_fake(monkeypatch, [])
    client = OpenverseImageClient(_cfg())
    with pytest.raises(ProviderUnavailableError):
        await client.generate_image(ImageRequest(query="nothing here"), tmp_path / "x.png", _ctx())


async def test_auto_raises_on_http_error(monkeypatch, tmp_path: Path):
    _install_fake(monkeypatch, [], raise_on_search=httpx.ConnectError("boom"))
    client = OpenverseImageClient(_cfg())
    with pytest.raises(ProviderUnavailableError):
        await client.generate_image(ImageRequest(query="anything"), tmp_path / "x.png", _ctx())


async def test_auto_dedup_avoids_same_id_within_episode(monkeypatch, tmp_path: Path):
    _install_fake(monkeypatch, [_item("only-1", license_name="cc0")])
    client = OpenverseImageClient(_cfg())
    used: set[str] = set()
    ctx = _ctx(extra={"openverse_used": used})

    await client.generate_image(ImageRequest(query="crab"), tmp_path / "1.png", ctx)
    assert "only-1" in used
    with pytest.raises(ProviderUnavailableError):
        await client.generate_image(ImageRequest(query="crab"), tmp_path / "2.png", ctx)


async def test_auto_query_priority_uses_image_query_first(monkeypatch, tmp_path: Path):
    holder = _install_fake(monkeypatch, [_item("x", license_name="cc0")])
    client = OpenverseImageClient(_cfg())
    await client.generate_image(
        ImageRequest(query="red knot bird flock", label="06_generic", size="16:9"),
        tmp_path / "06_generic.png",
        _ctx(),
    )
    assert holder["client"].search_calls[0]["q"] == "red knot bird flock"
    # the permissive license_type filter is requested
    assert holder["client"].search_calls[0]["license_type"] == "commercial"


async def test_auto_query_fallback_to_deslugged_label(monkeypatch, tmp_path: Path):
    holder = _install_fake(monkeypatch, [_item("x", license_name="cc0")])
    client = OpenverseImageClient(_cfg())
    await client.generate_image(
        ImageRequest(label="06_red_knot_bird", size="16:9"),
        tmp_path / "06_red_knot_bird.png",
        _ctx(),
    )
    assert holder["client"].search_calls[0]["q"] == "red knot bird"


# --------------------------------------------------------------------------- #
# 1b. Unit — search_candidates + download_chosen                              #
# --------------------------------------------------------------------------- #
async def test_search_candidates_limits_filters_and_carries_attribution(monkeypatch):
    items = [_item(f"good-{i}", license_name="by", creator=f"A{i}") for i in range(12)]
    items.append(_item("nc-1", license_name="by-nc"))  # rejected by license filter
    holder = _install_fake(monkeypatch, items)
    client = OpenverseImageClient(_cfg())

    cands = await client.search_candidates("horseshoe crab beach", _ctx(), size="16:9", limit=9)

    assert len(cands) == 9  # limit honoured
    ids = {c.id for c in cands}
    assert "nc-1" not in ids
    first = cands[0]
    assert first.thumb_url == "https://example.org/thumb.jpg"
    assert first.full_url == "https://example.org/full.jpg"
    assert first.license == "by"
    assert first.author == "A0"
    assert first.media_type == "image"
    # NOTHING downloaded during candidate search.
    assert holder["client"].download_calls == []


async def test_search_candidates_excludes_used_ids(monkeypatch):
    items = [_item("a", license_name="cc0"), _item("b", license_name="cc0")]
    _install_fake(monkeypatch, items)
    client = OpenverseImageClient(_cfg())
    cands = await client.search_candidates("crab", _ctx(), exclude={"a"})
    assert [c.id for c in cands] == ["b"]


async def test_search_candidates_empty_on_no_results(monkeypatch):
    _install_fake(monkeypatch, [])
    client = OpenverseImageClient(_cfg())
    assert await client.search_candidates("nothing", _ctx()) == []


async def test_search_candidates_empty_on_http_error(monkeypatch):
    _install_fake(monkeypatch, [], raise_on_search=httpx.ReadTimeout("slow"))
    client = OpenverseImageClient(_cfg())
    assert await client.search_candidates("anything", _ctx()) == []


async def test_download_chosen_writes_image_and_attribution(monkeypatch, tmp_path: Path):
    holder = _install_fake(monkeypatch, [])  # download path doesn't search
    client = OpenverseImageClient(_cfg())
    cand = ImageCandidate(
        id="pick-1",
        thumb_url="https://example.org/thumb.jpg",
        full_url="https://example.org/full.jpg",
        title="Pick",
        author="Ada L.",
        license="by",
        source_url="https://source.example/pick-1",
    )
    out = tmp_path / "01_crab.png"
    res = await client.download_chosen(cand, out, _ctx())

    assert res.out_path.exists() and res.out_path.stat().st_size >= 5000
    # The ORIGINAL (full) url was fetched, not the preview.
    assert holder["client"].download_calls == ["https://example.org/full.jpg"]
    attr = res.raw["attribution"]
    assert attr["license"] == "by" and attr["author"] == "Ada L."
    assert res.raw["chosen_id"] == "pick-1"
    assert res.raw["provider"] == "web-openverse"


async def test_download_chosen_raises_when_unfetchable(monkeypatch, tmp_path: Path):
    class _BadClient(_FakeAsyncClient):
        async def get(self, url, *, params=None, timeout=None):
            raise httpx.ConnectError("down")

    def _factory(**kw):
        return _BadClient(_results([]), **kw)

    monkeypatch.setattr(openverse_image.httpx, "AsyncClient", _factory)
    client = OpenverseImageClient(_cfg())
    cand = ImageCandidate(id="p", thumb_url="t", full_url="f", title="P")
    with pytest.raises(ProviderUnavailableError):
        await client.download_chosen(cand, tmp_path / "x.png", _ctx())


def test_supports_candidates_flag():
    assert OpenverseImageClient(_cfg()).supports_candidates is True
    assert OpenverseImageClient(_cfg()).requires_key is False


# --------------------------------------------------------------------------- #
# 2. Registry — resolve web-openverse keyless                                 #
# --------------------------------------------------------------------------- #
async def test_registry_resolves_web_openverse_without_key():
    reg = get_registry()
    ctx = _ctx()  # no keys at all
    client = await reg.resolve(Task.GENERATE_IMAGE, "web-openverse", ctx)
    assert client.provider_id == "web-openverse"
    assert client.requires_key is False
    assert "web-openverse" in reg._fallback["generate-image"]


def test_providers_endpoint_lists_web_openverse(client):
    resp = client.get("/providers")
    assert resp.status_code == 200
    image = resp.json()["image"]
    ov = next((o for o in image if o["id"] == "web-openverse"), None)
    assert ov is not None
    assert ov["cost_tier"] == "free" and ov["requires_key"] is False
