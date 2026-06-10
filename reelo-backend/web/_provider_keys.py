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


def provider_requires_sample(provider: str) -> bool:
    """Whether ``provider`` needs an uploaded voice-clone reference sample.

    True for a voice provider whose ``generate-voice`` task declares
    ``mode: clone`` (OmniVoice zero-shot cloning). Other voice providers
    (edge / eleven preset voices) and all non-voice providers return ``False``.
    """
    registry = get_registry()
    raw = registry.services_raw.get(provider)
    if not raw:
        return False
    voice_block = (raw.get("tasks") or {}).get(Task.GENERATE_VOICE.value) or {}
    return voice_block.get("mode") == "clone"


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


def provider_has_key(provider: str, present_refs: set[str]) -> bool:
    """Whether the user has a usable key for ``provider``.

    Keyless providers (edge / web-commons / omnivoice / the ``web`` aggregate /
    stubs) are always "have key". Otherwise the provider's ``auth.key_ref`` must
    be in ``present_refs`` (the set of key_refs the user has saved).
    """
    if not provider_requires_key(provider):
        return True
    return resolve_key_ref(provider) in present_refs


def first_ready_script_provider(present_refs: set[str]) -> str | None:
    """The first script provider the user can actually use (keyless or keyed).

    Used by the wizard when the request carries no per-series script provider yet
    (start of the create flow): pick a sensible default so the chat works instead
    of 409-ing. Stub providers are excluded. Returns ``None`` if nothing qualifies.
    """
    registry = get_registry()
    for provider_id, raw in registry.services_raw.items():
        if provider_id.startswith("stub-"):
            continue
        if Task.WRITE_SCRIPT.value not in (raw.get("tasks") or {}):
            continue
        if provider_has_key(provider_id, present_refs):
            return provider_id
    return None


def series_readiness(
    spec, present_refs: set[str]
) -> tuple[bool, bool, bool, list[str]]:
    """Resolve a series' readiness for chat/produce from its per-series toolset.

    Returns ``(script_ready, image_ready, voice_ready, missing[])``. A task is
    ready when its chosen provider needs no key OR the user has a per-user key for
    it; the voice task additionally needs a per-series voice sample when the
    provider is an OmniVoice-style clone. ``missing`` carries human messages for
    whatever blocks readiness (so the UI can route to the key page / sample
    upload).
    """
    providers = dict(getattr(spec, "providers", {}) or {})
    script = providers.get("script") or ""
    image = providers.get("image") or ""
    voice_provider = getattr(spec.voice, "provider", "") if getattr(spec, "voice", None) else ""

    missing: list[str] = []
    script_ready = bool(script) and provider_has_key(script, present_refs)
    if script and not script_ready:
        missing.append(f"Thiếu key cho provider kịch bản '{script}'.")
    image_ready = bool(image) and provider_has_key(image, present_refs)
    if image and not image_ready:
        missing.append(f"Thiếu key cho provider ảnh '{image}'.")

    voice_key_ok = (not voice_provider) or provider_has_key(voice_provider, present_refs)
    if voice_provider and not voice_key_ok:
        missing.append(f"Thiếu key cho provider giọng '{voice_provider}'.")
    sample_ok = True
    if voice_provider and provider_requires_sample(voice_provider):
        has_sample = bool(
            getattr(spec, "voice", None) and getattr(spec.voice, "voice_sample", None)
        )
        sample_ok = has_sample
        if not has_sample:
            missing.append("Cần tải lên giọng mẫu cho OmniVoice (clone).")
    voice_ready = bool(voice_provider) and voice_key_ok and sample_ok

    return script_ready, image_ready, voice_ready, missing


__all__ = [
    "TASK_TO_FIELD",
    "FIELD_TO_TASK",
    "provider_requires_key",
    "provider_requires_sample",
    "provider_supports_field",
    "provider_has_key",
    "first_ready_script_provider",
    "series_readiness",
    "resolve_key_ref",
    "build_validation_context",
    "client_for_key_ref",
]
