"""Reelo FastAPI application factory.

Wires: CORS (for reelo-ui), signed-cookie session (Authlib OAuth state + login
session), the auth router, and all REST routers (501 stubs in Phase 1). The app
boots cleanly without a live DB/Redis — endpoints fail at request time, not at
import/startup.

Run:  ``uvicorn web.app:app --reload``
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from config import get_settings
from web import auth
from web.routers import include_all

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing heavy (engine/redis pools are lazy).
    yield
    # Shutdown: dispose pooled resources.
    from db.session import dispose_engine
    from worker.enqueue import close_arq_pool

    await dispose_engine()
    await close_arq_pool()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Reelo API",
        version="0.1.0",
        description="Multi-tenant SaaS backend: idea → YouTube video via AI (BYOK).",
        lifespan=lifespan,
    )

    # Session cookie (Authlib stores OAuth state here; we store user_id post-login).
    # In prod the UI (cognal.xyz) and API (api.cognal.xyz) are different hostnames,
    # so scope the cookie to the parent domain (.cognal.xyz) and mark it Secure.
    # ``same_site="lax"`` is fine across subdomains of the same site (it only
    # restricts cross-*site* requests).
    session_kwargs: dict = dict(
        secret_key=settings.session_secret,
        session_cookie=settings.session_cookie_name,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.session_https_only,
    )
    if settings.session_cookie_domain_or_none:
        session_kwargs["domain"] = settings.session_cookie_domain_or_none
    app.add_middleware(SessionMiddleware, **session_kwargs)

    # CORS for the reelo-ui dev server (credentials so the session cookie flows).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router)
    include_all(app)
    return app


app = create_app()


__all__ = ["app", "create_app", "lifespan"]
