"""Account-level settings — PER-USER key management: GET /settings/providers.

Provider selection is per-series now; this page only reports the per-task
provider catalog + per-user key status. No live DB: ``ApiKeyRepo`` is replaced
with an in-memory fake, ``get_db`` yields a dummy session, and auth is overridden
to a fake user. The provider catalog comes from the real services.yaml (registry).
"""

from __future__ import annotations

import pytest

import web.routers.settings as settings_router
from web.deps import get_current_user, get_db

FAKE_USER_ID = "u_test"


class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeApiKeyStore:
    def __init__(self) -> None:
        # user_id -> {key_ref: valid}
        self.refs: dict[str, dict[str, bool | None]] = {}


class FakeApiKeyRepo:
    store: FakeApiKeyStore

    def __init__(self, session) -> None:
        pass

    async def list_refs(self, user_id):
        return [
            _Row(key_ref=ref, valid=valid)
            for ref, valid in self.store.refs.get(user_id, {}).items()
        ]


@pytest.fixture()
def s_client(monkeypatch):
    from fastapi.testclient import TestClient

    from web.app import create_app

    api_store = FakeApiKeyStore()
    FakeApiKeyRepo.store = api_store

    monkeypatch.setattr(settings_router, "ApiKeyRepo", FakeApiKeyRepo)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID

    async def _fake_db():
        yield object()

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.api_store = api_store  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def _by_id(items, provider_id):
    return next(i for i in items if i["id"] == provider_id)


def test_catalog_grouped_by_task(s_client):
    body = s_client.get("/settings/providers").json()
    assert {p["id"] for p in body["script"]} >= {"gemini", "claude"}
    assert {p["id"] for p in body["image"]} >= {"kie", "web-commons"}
    assert {p["id"] for p in body["voice"]} >= {"edge", "omnivoice"}
    # stubs hidden
    assert all(not p["id"].startswith("stub-") for grp in body.values() for p in grp)


def test_keyless_providers_have_key_no_requirement(s_client):
    body = s_client.get("/settings/providers").json()
    edge = _by_id(body["voice"], "edge")
    assert edge["requires_key"] is False
    assert edge["has_key"] is True  # nothing to enter
    assert edge["valid"] is None
    web_commons = _by_id(body["image"], "web-commons")
    assert web_commons["requires_key"] is False
    assert web_commons["has_key"] is True


def test_keyed_provider_without_key(s_client):
    body = s_client.get("/settings/providers").json()
    gemini = _by_id(body["script"], "gemini")
    assert gemini["requires_key"] is True
    assert gemini["has_key"] is False
    assert gemini["key_ref"] == "google_aistudio"


def test_keyed_provider_reflects_saved_key_and_validity(s_client):
    # Simulate a saved (validated) key under gemini's key_ref.
    s_client.api_store.refs[FAKE_USER_ID] = {"google_aistudio": True}  # type: ignore[attr-defined]
    body = s_client.get("/settings/providers").json()
    gemini = _by_id(body["script"], "gemini")
    assert gemini["has_key"] is True
    assert gemini["valid"] is True


def test_settings_requires_auth():
    from fastapi.testclient import TestClient

    from web.app import create_app

    anon = TestClient(create_app())
    assert anon.get("/settings/providers").status_code == 401
