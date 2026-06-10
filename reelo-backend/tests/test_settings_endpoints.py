"""Account-level provider settings: GET/PUT /settings/providers + readiness.

No live DB: ``UserSettingsRepo`` / ``ApiKeyRepo`` are replaced with in-memory
fakes, ``get_db`` yields a dummy session, and auth is overridden to a fake user.
The provider catalog comes from the real services.yaml (registry).
"""

from __future__ import annotations

import pytest

import web.routers.settings as settings_router
from web.deps import get_current_user, get_db

FAKE_USER_ID = "u_test"


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeSettingsStore:
    def __init__(self) -> None:
        self.providers: dict[str, dict[str, str | None]] = {}


class FakeUserSettingsRepo:
    store: FakeSettingsStore

    DEFAULTS = {"script": None, "image": None, "voice": "edge"}

    def __init__(self, session) -> None:
        pass

    @staticmethod
    def default_providers():
        return dict(FakeUserSettingsRepo.DEFAULTS)

    async def get_providers(self, user_id):
        merged = dict(self.DEFAULTS)
        merged.update(self.store.providers.get(user_id, {}))
        return merged

    async def set_providers(self, user_id, providers):
        cur = dict(self.store.providers.get(user_id, {}))
        cur.update(providers)
        self.store.providers[user_id] = cur
        merged = dict(self.DEFAULTS)
        merged.update(cur)
        return merged


class FakeApiKeyStore:
    def __init__(self) -> None:
        self.refs: dict[str, set[str]] = {}


class FakeApiKeyRepo:
    store: FakeApiKeyStore

    def __init__(self, session) -> None:
        pass

    async def list_refs(self, user_id):
        return [_Row(key_ref=r) for r in self.store.refs.get(user_id, set())]


@pytest.fixture()
def s_client(monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    settings_store = FakeSettingsStore()
    api_store = FakeApiKeyStore()
    FakeUserSettingsRepo.store = settings_store
    FakeApiKeyRepo.store = api_store

    monkeypatch.setattr(settings_router, "UserSettingsRepo", FakeUserSettingsRepo)
    monkeypatch.setattr(settings_router, "ApiKeyRepo", FakeApiKeyRepo)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.settings_store = settings_store  # type: ignore[attr-defined]
    client.api_store = api_store  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def test_get_defaults_voice_edge_script_image_unset(s_client):
    body = s_client.get("/settings/providers").json()
    assert body["script"]["provider"] is None
    assert body["image"]["provider"] is None
    assert body["voice"]["provider"] == "edge"
    # edge is keyless → ready; script/image unset → not ready
    assert body["voice_ready"] is True
    assert body["script_ready"] is False
    assert body["image_ready"] is False
    # catalog carried inline
    assert {p["id"] for p in body["options"]["script"]} >= {"gemini", "claude"}


def test_put_sets_providers_and_readiness(s_client):
    # web image alias is keyless → ready immediately; gemini script needs a key.
    resp = s_client.put(
        "/settings/providers", json={"script": "gemini", "image": "web"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["script"]["provider"] == "gemini"
    assert body["script"]["requires_key"] is True
    assert body["script"]["has_key"] is False
    assert body["script_ready"] is False  # no key yet
    assert body["image"]["provider"] == "web"
    assert body["image_ready"] is True  # keyless aggregate


def test_script_ready_once_key_present(s_client):
    s_client.put("/settings/providers", json={"script": "gemini"})
    # simulate a saved key under gemini's key_ref (google_aistudio)
    s_client.api_store.refs[FAKE_USER_ID] = {"google_aistudio"}  # type: ignore[attr-defined]
    body = s_client.get("/settings/providers").json()
    assert body["script"]["has_key"] is True
    assert body["script_ready"] is True


def test_put_rejects_wrong_task_provider(s_client):
    # edge is a voice provider; not valid for the script field
    resp = s_client.put("/settings/providers", json={"script": "edge"})
    assert resp.status_code == 400


def test_put_partial_keeps_other_fields(s_client):
    s_client.put("/settings/providers", json={"script": "claude", "image": "kie"})
    s_client.put("/settings/providers", json={"voice": "eleven"})
    body = s_client.get("/settings/providers").json()
    assert body["script"]["provider"] == "claude"
    assert body["image"]["provider"] == "kie"
    assert body["voice"]["provider"] == "eleven"


def test_settings_requires_auth():
    from fastapi.testclient import TestClient

    from web.app import create_app

    anon = TestClient(create_app())
    assert anon.get("/settings/providers").status_code == 401
    assert anon.put("/settings/providers", json={"script": "gemini"}).status_code == 401
