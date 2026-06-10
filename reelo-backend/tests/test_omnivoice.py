"""OmniVoice voice-clone provider — client, upload endpoint, module-2 wiring.

No GPU / torch here: the OmniVoice service is mocked. The mocked ``/clone`` body
is a REAL wav built with ffmpeg lavfi so the client's wav→mp3 transcode is
exercised for real (ffprobe must read the resulting mp3). Tests that need ffmpeg
skip cleanly when it is absent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clients.base import (
    CallContext,
    ProviderUnavailableError,
    ServiceConfig,
    Task,
    VoiceRequest,
)
from clients.omnivoice import OmniVoiceClient
from keystore import Cipher, KeyStore
from module2 import ffmpeg
from usage import UsageLogger

requires_ffmpeg = pytest.mark.skipif(
    not ffmpeg.ffmpeg_available(), reason="ffmpeg/ffprobe not installed"
)

_CFG = ServiceConfig(
    provider_id="omnivoice",
    raw={
        "auth": {"type": "none"},
        "cost_tier": "paid",
        "endpoint": "http://omnivoice.test:8002",
        "tasks": {"generate-voice": {"mode": "clone", "char_limit": 6000}},
        "pricing": {"generate-voice": {"per_1k_chars": 0.0}},
    },
)


def _ctx() -> CallContext:
    return CallContext(user_id="u1", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())


def _make_wav(path: Path, *, seconds: float = 1.0) -> bytes:
    """Build a real 24 kHz mono wav with ffmpeg lavfi and return its bytes (sync)."""
    argv = [
        ffmpeg.ffmpeg_bin(), "-y",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
        "-ar", "24000", "-ac", "1", str(path),
    ]
    subprocess.run(argv, check=True, capture_output=True)
    return path.read_bytes()


# --------------------------------------------------------------------------- #
# Fake httpx layer                                                            #
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b"", json_body=None):
        self.status_code = status_code
        self.content = content
        self._json = json_body
        self.text = "" if json_body is None else str(json_body)

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; returns a queued response and records the post."""

    last_post: dict = {}

    def __init__(self, response: _FakeResponse):
        self._response = response

    def __call__(self, *a, **kw):  # AsyncClient(timeout=...) -> context manager
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, files=None, data=None):
        # `files["ref_audio"]` is a (name, fileobj, ctype) tuple; read the bytes.
        ref = files["ref_audio"] if files else None
        ref_bytes = ref[1].read() if ref else b""
        _FakeAsyncClient.last_post = {
            "url": url, "data": dict(data or {}), "ref_len": len(ref_bytes),
        }
        return self._response

    async def get(self, url):
        return self._response


# --------------------------------------------------------------------------- #
# is_available / base url                                                     #
# --------------------------------------------------------------------------- #
async def test_is_available_uses_endpoint():
    c = OmniVoiceClient(_CFG)
    assert c._base_url() == "http://omnivoice.test:8002"
    assert await c.is_available(_ctx()) is True


async def test_is_available_false_without_url(monkeypatch):
    cfg = ServiceConfig("omnivoice", {"auth": {"type": "none"}, "tasks": {"generate-voice": {}}})
    c = OmniVoiceClient(cfg)
    # No endpoint in YAML and no OMNIVOICE_URL env -> unavailable.
    monkeypatch.setenv("OMNIVOICE_URL", "")
    from config import get_settings

    get_settings.cache_clear()
    assert c._base_url() is None
    assert await c.is_available(_ctx()) is False
    get_settings.cache_clear()


def test_client_flags():
    c = OmniVoiceClient(_CFG)
    assert c.cost_tier == "paid"
    assert c.requires_key is False
    assert c.supports(Task.GENERATE_VOICE)


# --------------------------------------------------------------------------- #
# generate_voice: wav -> mp3 (real transcode)                                 #
# --------------------------------------------------------------------------- #
@requires_ffmpeg
async def test_generate_voice_transcodes_to_mp3(tmp_path, monkeypatch):
    ref = tmp_path / "ref.wav"
    _make_wav(ref, seconds=2.0)
    clone_wav = _make_wav(tmp_path / "clone.wav", seconds=1.0)

    # The client imports httpx lazily inside generate_voice, so patch the module.
    import httpx

    monkeypatch.setattr(
        httpx, "AsyncClient", _FakeAsyncClient(_FakeResponse(200, content=clone_wav))
    )

    c = OmniVoiceClient(_CFG)
    out = tmp_path / "voice_part_01.mp3"
    req = VoiceRequest(
        voice_id="ignored", text="Xin chào, đây là giọng nói clone.",
        ref_audio=ref, ref_text="câu mẫu tham chiếu", language="vi",
    )
    result = await c.generate_voice(req, out, _ctx())

    assert result.out_path == out and out.exists()
    assert result.chars == len(req.text)
    # ffprobe must read the produced mp3 as a valid media file with a duration.
    dur = await ffmpeg.probe_duration(out)
    assert dur > 0
    # multipart carried ref_text + language + the ref wav bytes.
    posted = _FakeAsyncClient.last_post
    assert posted["data"]["ref_text"] == "câu mẫu tham chiếu"
    assert posted["data"]["language"] == "vi"
    assert posted["ref_len"] > 0


async def test_generate_voice_service_error_raises(tmp_path, monkeypatch):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFFfake")
    import httpx

    monkeypatch.setattr(
        httpx, "AsyncClient",
        _FakeAsyncClient(_FakeResponse(503, json_body={"detail": "no gpu"})),
    )
    c = OmniVoiceClient(_CFG)
    with pytest.raises(ProviderUnavailableError) as ei:
        await c.generate_voice(
            VoiceRequest(voice_id="x", text="hello", ref_audio=ref, ref_text="r"),
            tmp_path / "o.mp3", _ctx(),
        )
    assert "503" in str(ei.value)


async def test_generate_voice_missing_sample_raises(tmp_path):
    c = OmniVoiceClient(_CFG)
    with pytest.raises(ProviderUnavailableError):
        await c.generate_voice(
            VoiceRequest(voice_id="x", text="hello"),  # no ref_audio
            tmp_path / "o.mp3", _ctx(),
        )


async def test_generate_voice_no_url_raises(tmp_path, monkeypatch):
    cfg = ServiceConfig("omnivoice", {"auth": {"type": "none"}, "tasks": {"generate-voice": {}}})
    monkeypatch.setenv("OMNIVOICE_URL", "")
    from config import get_settings

    get_settings.cache_clear()
    c = OmniVoiceClient(cfg)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFFfake")
    with pytest.raises(ProviderUnavailableError):
        await c.generate_voice(
            VoiceRequest(voice_id="x", text="hi", ref_audio=ref, ref_text="r"),
            tmp_path / "o.mp3", _ctx(),
        )
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Upload endpoint: POST /series/{id}/voice-sample                             #
# --------------------------------------------------------------------------- #
from models.spec import ImageStyle, SeriesSpec, VoiceConfig  # noqa: E402


def _spec() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="n", topic="t", skill="religion", language="vi",
        target_minutes=5, density="standard",
        providers={"script": "stub-script", "image": "stub-image", "voice": "edge"},
        image_style=ImageStyle(preset_id="p", base_prompt="b"),
        voice=VoiceConfig(provider="edge", voice_id="vi-VN"),
    )


class _SeriesRow:
    def __init__(self, spec: SeriesSpec | None):
        self.spec_json = spec.model_dump() if spec else {}
        self.id = spec.series_id if spec else None


class _FakeSeriesRepo:
    def __init__(self, store):
        self.store = store

    async def get(self, user_id, series_id):
        sp = self.store["series"].get(series_id)
        return _SeriesRow(sp) if sp else None


class _FakeStorage:
    def __init__(self, store):
        self.store = store

    async def put(self, key, data, **kw):
        self.store["puts"][key] = data
        return key


@pytest.fixture()
def vs_client(monkeypatch):
    from fastapi.testclient import TestClient

    import web.routers.series as series_router
    from web.app import create_app
    from web.deps import get_current_user, get_db

    store = {"series": {}, "puts": {}}
    monkeypatch.setattr(series_router, "SeriesRepo", lambda s: _FakeSeriesRepo(store))
    monkeypatch.setattr(series_router, "get_storage", lambda: _FakeStorage(store))

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: "u_test"

    class _FakeDb:
        async def flush(self):
            return None

    async def _fake_db():
        yield _FakeDb()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.store = store  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


@requires_ffmpeg
def test_upload_voice_sample_sets_clone_config(vs_client, tmp_path):
    store = vs_client.store
    store["series"]["s1"] = _spec()
    wav = _make_wav(tmp_path / "u.wav", seconds=5.0)

    resp = vs_client.post(
        "/series/s1/voice-sample",
        files={"audio": ("u.wav", wav, "audio/wav")},
        data={"transcript": "đây là câu mẫu của tôi", "language": "vi"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["audio_key"] == "voice-samples/u_test/s1/sample.wav"
    assert 4.0 < body["duration_s"] < 6.0
    assert body["voice"]["provider"] == "omnivoice"
    assert body["voice"]["mode"] == "clone"
    assert body["voice"]["voice_sample"]["transcript"] == "đây là câu mẫu của tôi"
    assert body["voice"]["voice_sample"]["language"] == "vi"
    # sample stored as wav 24k mono
    assert store["puts"][body["audio_key"]][:4] == b"RIFF"


@requires_ffmpeg
def test_upload_voice_sample_rejects_too_short(vs_client, tmp_path):
    store = vs_client.store
    store["series"]["s1"] = _spec()
    wav = _make_wav(tmp_path / "short.wav", seconds=1.0)  # < 3s
    resp = vs_client.post(
        "/series/s1/voice-sample",
        files={"audio": ("short.wav", wav, "audio/wav")},
        data={"transcript": "x"},
    )
    assert resp.status_code == 400
    assert "3" in resp.json()["detail"]


def test_upload_voice_sample_404_missing_series(vs_client):
    resp = vs_client.post(
        "/series/nope/voice-sample",
        files={"audio": ("u.wav", b"RIFFxx", "audio/wav")},
        data={"transcript": "x"},
    )
    assert resp.status_code == 404


def test_upload_voice_sample_400_empty_transcript(vs_client):
    vs_client.store["series"]["s1"] = _spec()
    resp = vs_client.post(
        "/series/s1/voice-sample",
        files={"audio": ("u.wav", b"RIFFxx", "audio/wav")},
        data={"transcript": "   "},
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Module 2 voice orchestration in clone mode (mock client + fake storage)      #
# --------------------------------------------------------------------------- #
from clients.base import AIClient, VoiceResult  # noqa: E402
from module2 import voice as voice_mod  # noqa: E402
from module2.materialize import layout_for  # noqa: E402


class _CloneRecordingClient(AIClient):
    """Records the clone fields each chunk request carried; writes a tiny part."""

    capabilities = {Task.GENERATE_VOICE}
    requires_key = False

    def __init__(self, config: ServiceConfig):
        super().__init__(config)
        self.reqs: list[VoiceRequest] = []

    async def is_available(self, ctx):
        return True

    async def generate_voice(self, req: VoiceRequest, out_path, ctx) -> VoiceResult:
        self.reqs.append(req)
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"part")
        return VoiceResult(out_path=Path(out_path), chars=len(req.text or ""))


class _FakeRegistry:
    def __init__(self, client):
        self._client = client

    async def resolve(self, task, preferred, ctx):
        return self._client


async def test_synth_voice_clone_mode_threads_sample(tmp_path, monkeypatch):
    from models.spec import VoiceSample

    lo = layout_for(tmp_path)
    lo.voice_dir.mkdir(parents=True)
    lo.script_md.write_text("\n\n===\n\n".join(["a" * 80, "b" * 80]))

    # Fake storage: writes the ref wav the orchestrator downloads.
    class _Stg:
        async def get_to_file(self, key, path):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"RIFFsample")
            return Path(path)

    monkeypatch.setattr(voice_mod, "get_storage", lambda: _Stg())

    async def fake_concat(parts, out_path, **kw):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"joined")
        return Path(out_path)

    async def fake_probe(path, **kw):
        return 12.0

    monkeypatch.setattr(voice_mod.ffmpeg, "concat_audio", fake_concat)
    monkeypatch.setattr(voice_mod.ffmpeg, "probe_duration", fake_probe)

    series = _spec()
    series.voice = VoiceConfig(
        provider="omnivoice", voice_id="ignored", mode="clone",
        voice_sample=VoiceSample(
            audio_key="voice-samples/u/s1/sample.wav", transcript="ref transcript", language="vi"
        ),
    )
    client = _CloneRecordingClient(
        ServiceConfig("omnivoice", {"tasks": {"generate-voice": {"char_limit": 250}}})
    )

    outcome = await voice_mod.synth_voice(
        series, lo, _ctx(), registry=_FakeRegistry(client)
    )
    assert outcome.voice_mp3.exists()
    # both chunks carried the same downloaded ref + transcript + language.
    assert client.reqs and all(r.ref_text == "ref transcript" for r in client.reqs)
    assert all(r.language == "vi" for r in client.reqs)
    assert all(r.ref_audio == lo.voice_dir / "ref_sample.wav" for r in client.reqs)
    assert (lo.voice_dir / "ref_sample.wav").read_bytes() == b"RIFFsample"


async def test_synth_voice_preset_mode_no_clone_fields(tmp_path, monkeypatch):
    """Preset (edge) mode leaves clone fields None — backward-compatible."""
    lo = layout_for(tmp_path)
    lo.voice_dir.mkdir(parents=True)
    lo.script_md.write_text("hello narration")

    async def fake_concat(parts, out_path, **kw):
        Path(out_path).write_bytes(b"joined")
        return Path(out_path)

    async def fake_probe(path, **kw):
        return 5.0

    monkeypatch.setattr(voice_mod.ffmpeg, "concat_audio", fake_concat)
    monkeypatch.setattr(voice_mod.ffmpeg, "probe_duration", fake_probe)

    client = _CloneRecordingClient(ServiceConfig("edge", {"tasks": {"generate-voice": {}}}))
    await voice_mod.synth_voice(_spec(), lo, _ctx(), registry=_FakeRegistry(client))
    assert client.reqs and all(r.ref_audio is None for r in client.reqs)


# --------------------------------------------------------------------------- #
# Reference microservice server.py (mock mode — import-clean, no torch)        #
# --------------------------------------------------------------------------- #
def test_omnivoice_server_mock_imports_and_clones(monkeypatch):
    """server.py loads + /clone returns a valid wav under OMNIVOICE_MOCK=1 (no GPU)."""
    import importlib
    import sys

    monkeypatch.setenv("OMNIVOICE_MOCK", "1")
    repo_root = Path(__file__).resolve().parent.parent
    svc_dir = repo_root / "services" / "omnivoice"
    sys.path.insert(0, str(svc_dir))
    try:
        server = importlib.import_module("server")
        importlib.reload(server)
        from fastapi.testclient import TestClient

        c = TestClient(server.app)
        assert c.get("/health").json()["mock"] is True
        r = c.post(
            "/clone",
            files={"ref_audio": ("ref.wav", b"RIFFxxxx", "audio/wav")},
            data={"ref_text": "reference", "text": "target text", "language": "en"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/wav"
        assert r.content[:4] == b"RIFF"
    finally:
        sys.path.remove(str(svc_dir))
        sys.modules.pop("server", None)
