"""Service registry / factory (Module 3 §5).

Reads ``services.yaml``, imports each provider's dotted ``client`` class, builds
one stateless instance per provider, and resolves a client for a given
``(task, preferred provider, CallContext)`` with **BYOK-aware fallback**: the
fallback chain only includes providers the current user actually has a key for
(and whose client supports the task and is available).

Adding a provider = a YAML block + an entry in ``routing.fallback``; no change
here (module-3 §6).

The process-wide singleton is built lazily from the bundled ``services.yaml``;
override the path with ``REELO_SERVICES_YAML`` (tests/alt catalogs).
"""

from __future__ import annotations

import importlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from clients.base import (
    AIClient,
    CallContext,
    ProviderUnavailableError,
    ServiceConfig,
    Task,
)

DEFAULT_YAML = Path(__file__).resolve().parent.parent / "services.yaml"


def _import_dotted(path: str) -> type[AIClient]:
    """Import ``pkg.mod.ClassName`` and return the class."""
    module_path, _, cls_name = path.rpartition(".")
    if not module_path:
        raise ValueError(f"Invalid client path: {path!r}")
    module = importlib.import_module(module_path)
    cls = getattr(module, cls_name)
    if not issubclass(cls, AIClient):
        raise TypeError(f"{path} is not an AIClient subclass")
    return cls


class ServiceRegistry:
    """Loads the catalog and resolves clients (BYOK-aware)."""

    def __init__(self, yaml_path: str | Path | None = None) -> None:
        self._path = Path(yaml_path) if yaml_path else DEFAULT_YAML
        cfg = yaml.safe_load(self._path.read_text(encoding="utf-8")) or {}
        self._raw: dict[str, Any] = cfg
        self._fallback: dict[str, list[str]] = (cfg.get("routing", {}) or {}).get("fallback", {}) or {}
        self._clients: dict[str, AIClient] = {}
        for pid, sc in (cfg.get("services", {}) or {}).items():
            client_path = sc.get("client")
            if not client_path:
                continue
            cls = _import_dotted(client_path)
            self._clients[pid] = cls(ServiceConfig(provider_id=pid, raw=sc))

    # ---- lookup ------------------------------------------------------------
    @property
    def services_raw(self) -> dict[str, Any]:
        """The raw ``services:`` mapping (for ``/providers`` derivation)."""
        return self._raw.get("services", {}) or {}

    def get(self, provider_id: str) -> AIClient:
        client = self._clients.get(provider_id)
        if client is None:
            raise ProviderUnavailableError(f"Unknown provider: {provider_id}")
        return client

    def try_get(self, provider_id: str) -> AIClient | None:
        return self._clients.get(provider_id)

    def for_capability(self, task: Task) -> list[AIClient]:
        """Every registered client that declares support for ``task``."""
        return [c for c in self._clients.values() if c.supports(task)]

    def key_ref_for_provider(self, provider_id: str) -> str | None:
        """Map a provider id to its ``auth.key_ref`` (UI id ↔ key_ref, §13.1)."""
        client = self._clients.get(provider_id)
        if client is None:
            return None
        return client.config.auth.key_ref

    def provider_for_key_ref(self, key_ref: str) -> str | None:
        """Reverse map: first provider whose ``auth.key_ref`` equals ``key_ref``."""
        for pid, client in self._clients.items():
            if client.config.auth.key_ref == key_ref:
                return pid
        return None

    # ---- resolution --------------------------------------------------------
    async def resolve(self, task: Task, preferred: str, ctx: CallContext) -> AIClient:
        """Return a usable client for ``task``, preferring ``preferred``.

        Tries ``preferred`` first, then the YAML fallback chain — but only
        providers the user has a key for (or keyless ones) that support the task
        and report ``is_available``. Raises :class:`ProviderUnavailableError`
        with an actionable message if none qualify (BYOK-aware, §5).
        """
        chain = [preferred] + [p for p in self._fallback.get(task.value, []) if p != preferred]
        for pid in chain:
            client = self._clients.get(pid)
            if client is None:
                continue
            if not client.supports(task):
                continue
            if await client.is_available(ctx):
                return client
        raise ProviderUnavailableError(
            f"No available provider for {task.value}: configure a key for one of "
            f"{', '.join(chain)} (preferred: {preferred})."
        )


@lru_cache(maxsize=1)
def get_registry() -> ServiceRegistry:
    """Process-wide registry singleton (override path via ``REELO_SERVICES_YAML``)."""
    return ServiceRegistry(os.environ.get("REELO_SERVICES_YAML") or DEFAULT_YAML)


__all__ = ["ServiceRegistry", "get_registry", "DEFAULT_YAML"]
