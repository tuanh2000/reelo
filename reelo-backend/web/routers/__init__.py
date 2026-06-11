"""HTTP routers.

Each router declares its endpoints with the correct request/response models
(matching ``reelo-ui`` + the module specs) and returns HTTP 501 until the
owning module fills in logic. Routers are registered in ``web.app`` via
:func:`include_all`.

Ownership:
- ``wizard``, ``episodes``, ``style``, ``series``  → Module 1 (reelo-scriptwriting)
- ``generation``, ``publish``, ``voices``           → Module 2 (reelo-video-generator)
- ``providers``, ``keys``, ``usage``, ``settings``  → Module 3 (reelo-ai-services)
- ``files`` (local-storage serving), ``health``     → platform-lead
"""

from __future__ import annotations

from fastapi import FastAPI

from web.routers import (
    episodes,
    files,
    generation,
    health,
    keys,
    providers,
    publish,
    series,
    settings,
    style,
    usage,
    voices,
    wizard,
)


def include_all(app: FastAPI) -> None:
    """Register every router on the app."""
    app.include_router(health.router)
    app.include_router(wizard.router)
    app.include_router(episodes.router)
    app.include_router(style.router)
    app.include_router(series.router)
    app.include_router(generation.router)
    app.include_router(publish.router)
    app.include_router(providers.router)
    app.include_router(settings.router)
    app.include_router(keys.router)
    app.include_router(usage.router)
    app.include_router(voices.router)
    app.include_router(files.router)


__all__ = ["include_all"]
