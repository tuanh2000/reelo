"""Global voice-pause control: the Redis flag + the settings endpoints.

The flag lets the user pause ALL voice synthesis (protect the shared local GPU
while several videos produce at once) without touching image/render. Redis is
faked with a dict so no live server is needed.
"""

from __future__ import annotations

import worker.control as control
from web.deps import get_current_user


class _FakeRedis:
    """Tiny async dict standing in for ArqRedis (get/set/delete)."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


# --------------------------------------------------------------------------- #
# control helpers                                                             #
# --------------------------------------------------------------------------- #
async def test_voice_pause_flag_roundtrip():
    r = _FakeRedis()
    assert await control.is_voice_paused(r) is False
    await control.set_voice_paused(r, True)
    assert await control.is_voice_paused(r) is True
    await control.set_voice_paused(r, False)
    assert await control.is_voice_paused(r) is False


async def test_is_voice_paused_no_redis_is_false():
    # No Redis (e.g. ctx without one) must degrade to "not paused", never block.
    assert await control.is_voice_paused(None) is False


async def test_is_voice_paused_read_error_is_false():
    class _Boom:
        async def get(self, key):
            raise RuntimeError("redis down")

    assert await control.is_voice_paused(_Boom()) is False


# --------------------------------------------------------------------------- #
# settings endpoints                                                          #
# --------------------------------------------------------------------------- #
def test_voice_pause_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    import web.routers.settings as settings_router
    from web.app import create_app

    fake = _FakeRedis()

    async def fake_pool():
        return fake

    monkeypatch.setattr(settings_router, "get_arq_pool", fake_pool)

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: "u_test"
    client = TestClient(app)

    assert client.get("/settings/voice-pause").json() == {"paused": False}
    assert client.post("/settings/voice-pause", json={"paused": True}).json() == {"paused": True}
    assert client.get("/settings/voice-pause").json() == {"paused": True}  # persisted in the flag
    assert client.post("/settings/voice-pause", json={"paused": False}).json() == {"paused": False}

    app.dependency_overrides.clear()
