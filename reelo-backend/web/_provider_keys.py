"""Helpers for the BYOK key endpoints (Module 3).

Validating a key on save needs a :class:`CallContext` whose :class:`KeyStore`
holds *just* the candidate key (it isn't persisted yet). These helpers build that
throwaway context and resolve provider → key_ref via the registry, so the
``/keys`` router stays thin.
"""

from __future__ import annotations

from clients.base import CallContext, Task
from clients.registry import get_registry
from keystore import KeyStore, build_cipher_from_settings
from usage import UsageLogger

# Task enum ↔ the per-task field names used across the API surface.
TASK_TO_FIELD: dict[Task, str] = {
    Task.WRITE_SCRIPT: "script",
    Task.GENERATE_IMAGE: "image",
    Task.GENERATE_VOICE: "voice",
}
FIELD_TO_TASK: dict[str, Task] = {v: k for k, v in TASK_TO_FIELD.items()}


def provider_requires_key(provider: str) -> bool:
    """Whether ``provider`` needs a BYOK key (auth.type != none).

    Unknown providers default to ``False`` (no key to demand). Keyless providers
    (edge, web-commons, omnivoice, stubs) return ``False``.
    """
    registry = get_registry()
    raw = registry.services_raw.get(provider)
    if not raw:
        return False
    auth = raw.get("auth", {}) or {}
    return auth.get("type", "key") != "none"


def provider_supports_field(provider: str, field: str) -> bool:
    """Whether ``provider`` declares support for the task behind ``field``.

    ``field`` is one of ``script`` / ``image`` / ``voice``. The aggregate web
    image alias ``"web"`` is accepted for the image field (it is resolved to the
    concrete web-* providers downstream).
    """
    if field == "image" and provider == "web":
        return True
    registry = get_registry()
    raw = registry.services_raw.get(provider)
    if not raw:
        return False
    task = FIELD_TO_TASK.get(field)
    if task is None:
        return False
    return task.value in (raw.get("tasks") or {})


def resolve_key_ref(provider: str) -> str:
    """Map a UI provider id to its ``auth.key_ref`` (§13.1).

    Falls back to the provider id itself when the provider is unknown (so a
    caller passing a raw key_ref still works).
    """
    registry = get_registry()
    return registry.key_ref_for_provider(provider) or provider


def build_validation_context(user_id: str, key_ref: str, value: str) -> CallContext:
    """A :class:`CallContext` whose store holds only ``value`` under ``key_ref``.

    Used by ``POST /keys`` to run ``client.validate_key`` before persisting.
    Usage is discarded (validation makes no billable call worth logging).
    """
    cipher = build_cipher_from_settings()
    keys = KeyStore(cipher)
    keys.save(user_id, key_ref, value)
    return CallContext(user_id=user_id, keys=keys, usage=UsageLogger())


def client_for_key_ref(key_ref: str):
    """Return the registry client whose ``auth.key_ref`` matches, or ``None``."""
    registry = get_registry()
    provider_id = registry.provider_for_key_ref(key_ref)
    if provider_id is None:
        return None
    return registry.try_get(provider_id)


__all__ = [
    "TASK_TO_FIELD",
    "FIELD_TO_TASK",
    "provider_requires_key",
    "provider_supports_field",
    "resolve_key_ref",
    "build_validation_context",
    "client_for_key_ref",
]
