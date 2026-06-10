"""Smoke tests: the app boots, health works, and stub endpoints behave."""

from __future__ import annotations


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_openapi_lists_all_endpoints(client):
    """Every contract endpoint is registered (catches accidental router drops)."""
    spec = client.get("/openapi.json").json()
    paths = set(spec["paths"].keys())
    expected = {
        "/wizard/message",
        "/wizard/approve",
        "/episodes/{episode_id}/script",
        "/style/infer",
        "/series",
        "/series/{series_id}",
        "/series/{series_id}/music",
        "/generation/start",
        "/generation/{job_id}",
        "/generation/{job_id}/retry/{child_id}",
        "/publish/export",
        "/providers",
        "/keys",
        "/keys/status",
        "/keys/{key_ref}",
        "/usage",
        "/auth/login",
        "/auth/callback",
        "/health",
    }
    missing = expected - paths
    assert not missing, f"missing endpoints: {missing}"


def test_module2_endpoints_no_longer_stubbed(app):
    """The Module 2 pipeline endpoints are implemented (no longer 501).

    Module 3 endpoints (/providers, /keys*, /usage) are implemented by
    reelo-ai-services; Module 1 endpoints (/wizard/*, /series, /episodes/*,
    /style/infer) by reelo-scriptwriting; Module 2 (/generation/*, /publish/*,
    /series/{id}/music) by reelo-video-generator. Their behaviour is covered with
    fakes in tests/test_module2_endpoints.py; here we only assert they are wired
    (any status other than 501). With auth overridden but no live DB, a real call
    fails at the DB layer (surfaced as 500 because server exceptions are not
    re-raised here) — what matters is it is not the stub.
    """
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    cases = [
        ("get", "/generation/abc", None),
        ("post", "/generation/start", {"series_id": "s1", "episode_id": "e1"}),
        ("post", "/publish/export", {
            "series_id": "s1",
            "episode_id": "e1",
            "meta": {"title": "t", "description": "d", "tags": [], "visibility": "public"},
        }),
    ]
    for method, path, json_body in cases:
        resp = getattr(client, method)(path, json=json_body) if json_body is not None else getattr(
            client, method
        )(path)
        assert resp.status_code != 501, f"{method} {path} still stubbed ({resp.text})"


def test_providers_implemented(client):
    """GET /providers now derives from services.yaml (Module 3)."""
    resp = client.get("/providers")
    assert resp.status_code == 200
    assert set(resp.json()) == {"script", "image", "voice"}


def test_protected_route_requires_auth(anon_client):
    """Without a session, protected routes return 401."""
    resp = anon_client.get("/series")
    assert resp.status_code == 401


def test_save_key_body_validation_when_authed(client):
    """Pydantic body validation runs before the handler: bad body -> 422.

    The success path (validate + encrypt + store) needs a DB + provider call and
    is covered with fakes in tests/test_module3_endpoints.py.
    """
    bad = client.post("/keys", json={"provider": "eleven"})  # missing 'key'
    assert bad.status_code == 422
