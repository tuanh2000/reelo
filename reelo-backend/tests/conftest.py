"""Test fixtures. No database or Redis required.

Sets minimal env (master key, session secret) *before* config is imported, so
the cached :class:`Settings` boots, then provides a FastAPI ``TestClient`` with
the auth dependency overridden to a fake user.
"""

from __future__ import annotations

import base64
import os

import pytest

# --- env must be set before config.get_settings() is first called -----------
os.environ.setdefault("REELO_ENV", "dev")
os.environ.setdefault("REELO_MASTER_KEY", base64.b64encode(b"0" * 32).decode())
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://reelo:reelo@localhost:5432/reelo")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from fastapi.testclient import TestClient  # noqa: E402

from web.app import create_app  # noqa: E402
from web.deps import get_current_user  # noqa: E402

FAKE_USER_ID = "u_test"


@pytest.fixture()
def app():
    application = create_app()
    application.dependency_overrides[get_current_user] = lambda: FAKE_USER_ID
    yield application
    application.dependency_overrides.clear()


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def anon_client():
    """Client with auth NOT overridden — to assert 401 on protected routes."""
    application = create_app()
    return TestClient(application)
