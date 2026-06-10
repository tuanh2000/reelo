"""Native kie.ai image client — async submit/poll/download (httpx faked).

``httpx.AsyncClient`` is replaced with an in-memory stand-in so we assert the
createTask → recordInfo → download lifecycle, the taskId plumbing, and the
state→exception mapping (gone vs pending) WITHOUT any network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clients.base import CallContext, ImageRequest, InvalidKeyError, ServiceConfig
from clients.kie_image import (
    KieImageClient,
    KieTaskGone,
    KieTaskPending,
    extract_image_url,
)
from keystore import Cipher, KeyStore
from usage import UsageLogger

_CFG = ServiceConfig(
    provider_id="kie",
    raw={
        "auth": {"type": "key", "key_ref": "kie", "env": "KIE_API_KEY"},
        "tasks": {"generate-image": {"default_size": "16:9"}},
        "pricing": {"generate-image": {"per_image": 0.0}},
    },
)


def _ctx(key: str | None = "sk-kie") -> CallContext:
    store = KeyStore(Cipher(b"k" * 32))
    if key is not None:
        store.save("u1", "kie", key)
    return CallContext(user_id="u1", keys=store, usage=UsageLogger())


# --------------------------------------------------------------------------- #
# httpx stand-in                                                              #
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status_code=200, payload=None, content=b"PNGBYTES"):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPError(f"HTTP {self.status_code}")


class _FakeHttp:
    def __init__(self, *, post=None, get=None):
        self._post, self._get = post, get

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        return self._post(url, kw)

    async def get(self, url, **kw):
        return self._get(url, kw)


def _patch(monkeypatch, *, post=None, get=None):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeHttp(post=post, get=get))


def _record_ok(state="success", url="http://img.kie/x.png"):
    return _Resp(200, {"code": 200, "data": {"state": state,
                                             "resultJson": json.dumps({"resultUrls": [url]})}})


# --------------------------------------------------------------------------- #
# submit                                                                      #
# --------------------------------------------------------------------------- #
async def test_submit_returns_task_id(monkeypatch):
    _patch(monkeypatch, post=lambda u, kw: _Resp(200, {"code": 200, "data": {"taskId": "t-42"}}))
    client = KieImageClient(_CFG)
    tid = await client.submit_image_task(ImageRequest(prompt="a cat", size="16:9"), _ctx())
    assert tid == "t-42"


async def test_submit_missing_key_raises():
    client = KieImageClient(_CFG)
    with pytest.raises(InvalidKeyError):
        await client.submit_image_task(ImageRequest(prompt="x"), _ctx(key=None))


async def test_submit_auth_error_maps_invalid_key(monkeypatch):
    _patch(monkeypatch, post=lambda u, kw: _Resp(401, {"msg": "bad key"}))
    client = KieImageClient(_CFG)
    with pytest.raises(InvalidKeyError):
        await client.submit_image_task(ImageRequest(prompt="x"), _ctx())


async def test_submit_code_error_raises_unavailable(monkeypatch):
    _patch(monkeypatch, post=lambda u, kw: _Resp(200, {"code": 422, "msg": "bad size"}))
    client = KieImageClient(_CFG)
    with pytest.raises(Exception) as ei:
        await client.submit_image_task(ImageRequest(prompt="x"), _ctx())
    assert not isinstance(ei.value, InvalidKeyError)


# --------------------------------------------------------------------------- #
# poll                                                                        #
# --------------------------------------------------------------------------- #
async def test_poll_success_downloads(monkeypatch, tmp_path):
    def get(url, kw):
        return _record_ok() if "recordInfo" in url else _Resp(200, content=b"REALPNG")

    _patch(monkeypatch, get=get)
    client = KieImageClient(_CFG)
    out = tmp_path / "img.png"
    res = await client.poll_image_task("t-1", out, _ctx(), max_wait=10, poll_interval=1)
    assert res.out_path == out
    assert out.read_bytes() == b"REALPNG"
    assert res.raw["task_id"] == "t-1"


async def test_poll_failure_state_raises_gone(monkeypatch, tmp_path):
    _patch(monkeypatch, get=lambda u, kw: _Resp(200, {"code": 200, "data": {"state": "failed", "failMsg": "nsfw"}}))
    client = KieImageClient(_CFG)
    with pytest.raises(KieTaskGone):
        await client.poll_image_task("t-1", tmp_path / "x.png", _ctx(), max_wait=10, poll_interval=1)


async def test_poll_unknown_task_raises_gone(monkeypatch, tmp_path):
    # code not in (200,0) → record can't be retrieved (expired/unknown) → gone.
    _patch(monkeypatch, get=lambda u, kw: _Resp(200, {"code": 404, "msg": "not found"}))
    client = KieImageClient(_CFG)
    with pytest.raises(KieTaskGone):
        await client.poll_image_task("t-gone", tmp_path / "x.png", _ctx(), max_wait=10, poll_interval=1)


async def test_poll_pending_times_out(monkeypatch, tmp_path):
    import asyncio as _aio

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(_aio, "sleep", _no_sleep)
    _patch(monkeypatch, get=lambda u, kw: _Resp(200, {"code": 200, "data": {"state": "generating"}}))
    client = KieImageClient(_CFG)
    with pytest.raises(KieTaskPending):
        await client.poll_image_task("t-slow", tmp_path / "x.png", _ctx(), max_wait=3, poll_interval=1)


# --------------------------------------------------------------------------- #
# one-shot + url extraction                                                   #
# --------------------------------------------------------------------------- #
async def test_generate_image_submit_then_poll(monkeypatch, tmp_path):
    def post(u, kw):
        return _Resp(200, {"code": 200, "data": {"taskId": "t-9"}})

    def get(u, kw):
        return _record_ok() if "recordInfo" in u else _Resp(200, content=b"PNG9")

    _patch(monkeypatch, post=post, get=get)
    client = KieImageClient(_CFG)
    out = tmp_path / "o.png"
    res = await client.generate_image(ImageRequest(prompt="hi", size="16:9"), out, _ctx())
    assert out.read_bytes() == b"PNG9"
    assert res.raw["task_id"] == "t-9"


def test_extract_image_url_shapes():
    assert extract_image_url({"resultJson": json.dumps({"resultUrls": ["http://a/x.png"]})}) == "http://a/x.png"
    assert extract_image_url({"resultJson": json.dumps(["http://b/y.png"])}) == "http://b/y.png"
    assert extract_image_url({"resultJson": "http://c/z.png"}) == "http://c/z.png"
    assert extract_image_url({"resultJson": json.dumps({"images": [{"url": "http://d/w.png"}]})}) == "http://d/w.png"
    assert extract_image_url({"resultJson": ""}) is None
