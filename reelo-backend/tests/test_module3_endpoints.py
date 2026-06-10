"""Module 3 web endpoints: /providers, /keys, /keys/status, DELETE, /usage.

No live DB: the ``ApiKeyRepo`` / ``UsageRepo`` the routers construct are replaced
with in-memory fakes, and ``get_db`` is overridden to yield a dummy session.
``validate_key`` is mocked (no real provider call).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import web.routers.keys as keys_router
import web.routers.usage as usage_router
from clients.base import InvalidKeyError, ProviderUnavailableError
from web.deps import get_current_user, get_db

FAKE_USER_ID = "u_test"


# --------------------------------------------------------------------------- #
# In-memory fakes for the repos the routers build from the session             #
# --------------------------------------------------------------------------- #
class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeApiKeyStore:
    """Process-wide store shared across a test's requests."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str], _Row] = {}


class FakeApiKeyRepo:
    store: FakeApiKeyStore  # set per test

    def __init__(self, session) -> None:
        pass

    async def list_refs(self, user_id):
        return [r for (uid, _), r in self.store.records.items() if uid == user_id]

    async def get(self, user_id, key_ref):
        return self.store.records.get((user_id, key_ref))

    async def upsert(self, *, user_id, key_ref, ciphertext, nonce, valid):
        row = _Row(
            user_id=user_id, key_ref=key_ref, ciphertext=ciphertext, nonce=nonce, valid=valid
        )
        self.store.records[(user_id, key_ref)] = row
        return row

    async def delete(self, user_id, key_ref):
        self.store.records.pop((user_id, key_ref), None)


class FakeUsageStore:
    def __init__(self) -> None:
        self.rows: list[_Row] = []


class FakeUsageRepo:
    store: FakeUsageStore

    def __init__(self, session) -> None:
        pass

    async def list_for_user(self, user_id):
        return [r for r in self.store.rows if r.user_id == user_id]


@pytest.fixture()
def m3_client(monkeypatch):
    """TestClient with auth + DB + repos faked for Module 3 endpoints."""
    from fastapi.testclient import TestClient

    from web.app import create_app

    api_store = FakeApiKeyStore()
    usage_store = FakeUsageStore()
    FakeApiKeyRepo.store = api_store
    FakeUsageRepo.store = usage_store

    monkeypatch.setattr(keys_router, "ApiKeyRepo", FakeApiKeyRepo)
    monkeypatch.setattr(usage_router, "UsageRepo", FakeUsageRepo)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID

    async def _fake_db():
        yield object()  # routers don't touch the real session through the fakes

    app.dependency_overrides[get_db] = _fake_db
    client = TestClient(app)
    client.api_store = api_store  # type: ignore[attr-defined]
    client.usage_store = usage_store  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# /providers                                                                   #
# --------------------------------------------------------------------------- #
def test_providers_grouped_by_task(m3_client):
    resp = m3_client.get("/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"script", "image", "voice"}
    script_ids = {p["id"] for p in body["script"]}
    voice_ids = {p["id"] for p in body["voice"]}
    image_ids = {p["id"] for p in body["image"]}
    assert {"claude", "chatgpt", "gemini", "deepseek"} <= script_ids
    assert {"eleven", "edge"} <= voice_ids
    assert {"kie"} <= image_ids
    # stubs hidden
    assert not any(i.startswith("stub-") for i in script_ids | voice_ids | image_ids)


def test_providers_shape_fields(m3_client):
    body = m3_client.get("/providers").json()
    edge = next(p for p in body["voice"] if p["id"] == "edge")
    assert edge["requires_key"] is False
    assert edge["cost_tier"] == "free"
    eleven = next(p for p in body["voice"] if p["id"] == "eleven")
    assert eleven["requires_key"] is True
    assert eleven["key_help_url"]


# --------------------------------------------------------------------------- #
# /keys POST (validate + store) and status / delete                           #
# --------------------------------------------------------------------------- #
def test_save_key_valid_then_status(m3_client, monkeypatch):
    from clients.skill_voice import SkillVoiceClient

    async def ok(self, ctx):
        return True

    monkeypatch.setattr(SkillVoiceClient, "validate_key", ok)

    resp = m3_client.post("/keys", json={"provider": "eleven", "key": "sk-live-123"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key_ref"] == "elevenlabs"  # mapped via auth.key_ref
    assert body["valid"] is True

    # stored encrypted (no plaintext)
    row = m3_client.api_store.records[(FAKE_USER_ID, "elevenlabs")]  # type: ignore[attr-defined]
    assert isinstance(row.ciphertext, bytes)
    assert b"sk-live-123" not in row.ciphertext

    status = m3_client.get("/keys/status").json()
    assert status["keys"]["elevenlabs"] == {"present": True, "valid": True}
    # never returns the value
    assert "sk-live-123" not in status["keys"]["elevenlabs"].keys()


def test_save_key_invalid_rejected_not_stored(m3_client, monkeypatch):
    from clients.skill_voice import SkillVoiceClient

    async def bad(self, ctx):
        raise InvalidKeyError("nope")

    monkeypatch.setattr(SkillVoiceClient, "validate_key", bad)

    resp = m3_client.post("/keys", json={"provider": "eleven", "key": "wrong"})
    assert resp.status_code == 400
    assert (FAKE_USER_ID, "elevenlabs") not in m3_client.api_store.records  # type: ignore[attr-defined]


def test_save_key_unverifiable_stored_with_valid_none(m3_client, monkeypatch):
    from clients.skill_voice import SkillVoiceClient

    async def down(self, ctx):
        raise ProviderUnavailableError("503")

    monkeypatch.setattr(SkillVoiceClient, "validate_key", down)

    resp = m3_client.post("/keys", json={"provider": "eleven", "key": "sk-unverified"})
    assert resp.status_code == 200
    assert resp.json()["valid"] is None
    assert (FAKE_USER_ID, "elevenlabs") in m3_client.api_store.records  # type: ignore[attr-defined]


def test_save_key_empty_rejected(m3_client):
    resp = m3_client.post("/keys", json={"provider": "eleven", "key": "   "})
    assert resp.status_code == 400


def test_delete_key(m3_client):
    m3_client.api_store.records[(FAKE_USER_ID, "elevenlabs")] = _Row(  # type: ignore[attr-defined]
        user_id=FAKE_USER_ID, key_ref="elevenlabs", ciphertext=b"x", nonce=b"y", valid=True
    )
    resp = m3_client.delete("/keys/elevenlabs")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "elevenlabs"}
    assert (FAKE_USER_ID, "elevenlabs") not in m3_client.api_store.records  # type: ignore[attr-defined]


def test_keys_status_empty(m3_client):
    assert m3_client.get("/keys/status").json() == {"keys": {}}


def test_save_key_requires_auth():
    """Without the auth override, /keys is 401 (uses real get_current_user)."""
    from fastapi.testclient import TestClient

    from web.app import create_app

    anon = TestClient(create_app())
    resp = anon.post("/keys", json={"provider": "eleven", "key": "x"})
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# /usage                                                                       #
# --------------------------------------------------------------------------- #
def test_usage_totals(m3_client):
    now = datetime.now(timezone.utc)
    m3_client.usage_store.rows.extend(  # type: ignore[attr-defined]
        [
            _Row(user_id=FAKE_USER_ID, provider="claude", task="write-script", units=1000, cost=0.02, ts=now),
            _Row(user_id=FAKE_USER_ID, provider="eleven", task="generate-voice", units=500, cost=0.15, ts=now),
            _Row(user_id="someone-else", provider="kie", task="generate-image", units=1, cost=0.0, ts=now),
        ]
    )
    body = m3_client.get("/usage").json()
    assert len(body["usage"]) == 2  # scoped to FAKE_USER_ID
    assert body["total_cost"] == pytest.approx(0.17)


def test_usage_empty_total_none(m3_client):
    body = m3_client.get("/usage").json()
    assert body["usage"] == []
    assert body["total_cost"] is None
