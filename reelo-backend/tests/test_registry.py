"""ServiceRegistry resolve + BYOK-aware fallback (Module 3).

No network: clients are resolved by capability/availability only, and
availability is driven by which keys the fake KeyStore reports present.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from clients.base import (
    AIClient,
    CallContext,
    ProviderUnavailableError,
    Task,
)
from clients.registry import ServiceRegistry, get_registry
from keystore import Cipher, KeyStore
from usage import UsageLogger


def _ctx(keys: KeyStore, user_id: str = "u1") -> CallContext:
    return CallContext(user_id=user_id, keys=keys, usage=UsageLogger())


def _keystore_with(*key_refs: str, user_id: str = "u1") -> KeyStore:
    store = KeyStore(Cipher(b"k" * 32))
    for ref in key_refs:
        store.save(user_id, ref, f"secret-{ref}")
    return store


# --------------------------------------------------------------------------- #
# A self-contained mini-catalog so the test doesn't depend on real client SDKs #
# --------------------------------------------------------------------------- #
def _write_catalog(tmp_path: Path) -> Path:
    yaml_text = textwrap.dedent(
        """
        services:
          claude:
            display_name: "Claude"
            client: "tests.test_registry.FakeKeyedClient"
            cost_tier: paid
            auth: { type: key, key_ref: "anthropic" }
            tasks: { write-script: {} }
          chatgpt:
            display_name: "ChatGPT"
            client: "tests.test_registry.FakeKeyedClient"
            cost_tier: paid
            auth: { type: key, key_ref: "openai" }
            tasks: { write-script: {} }
          gemini:
            display_name: "Gemini"
            client: "tests.test_registry.FakeKeyedClient"
            cost_tier: free
            auth: { type: key, key_ref: "google_aistudio" }
            tasks: { write-script: {} }
          edge:
            display_name: "Edge"
            client: "tests.test_registry.FakeKeylessClient"
            cost_tier: free
            auth: { type: none }
            tasks: { generate-voice: {} }
          eleven:
            display_name: "ElevenLabs"
            client: "tests.test_registry.FakeKeyedClient"
            cost_tier: paid
            auth: { type: key, key_ref: "elevenlabs" }
            tasks: { generate-voice: {} }
          down:
            display_name: "Always Down"
            client: "tests.test_registry.FakeDownClient"
            cost_tier: paid
            auth: { type: key, key_ref: "down" }
            tasks: { write-script: {} }
        routing:
          fallback:
            write-script: ["claude", "chatgpt", "gemini", "down"]
            generate-voice: ["eleven", "edge"]
            generate-image: []
        """
    )
    path = tmp_path / "services.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return path


class FakeKeyedClient(AIClient):
    """Keyed client; available iff the user has its key_ref. No network."""

    requires_key = True

    async def validate_key(self, ctx: CallContext) -> bool:
        return True


class FakeKeylessClient(AIClient):
    requires_key = False

    async def validate_key(self, ctx: CallContext) -> bool:
        return True


class FakeDownClient(AIClient):
    """Has a key but is never available (simulates an outage)."""

    requires_key = True

    async def is_available(self, ctx: CallContext) -> bool:
        return False

    async def validate_key(self, ctx: CallContext) -> bool:
        return True


@pytest.fixture()
def registry(tmp_path) -> ServiceRegistry:
    return ServiceRegistry(_write_catalog(tmp_path))


async def test_resolve_prefers_preferred_when_keyed(registry):
    keys = _keystore_with("anthropic", "openai")
    client = await registry.resolve(Task.WRITE_SCRIPT, "chatgpt", _ctx(keys))
    assert client.provider_id == "chatgpt"


async def test_resolve_falls_back_only_to_keyed_providers(registry):
    # User has no claude key but has gemini -> preferred claude unavailable,
    # fallback skips chatgpt (no key) and lands on gemini.
    keys = _keystore_with("google_aistudio")
    client = await registry.resolve(Task.WRITE_SCRIPT, "claude", _ctx(keys))
    assert client.provider_id == "gemini"


async def test_resolve_skips_unavailable_even_with_key(registry):
    # User has only the 'down' key; it supports the task and has a key, but
    # is_available() is False -> no provider resolves.
    keys = _keystore_with("down")
    with pytest.raises(ProviderUnavailableError):
        await registry.resolve(Task.WRITE_SCRIPT, "down", _ctx(keys))


async def test_resolve_raises_when_user_has_no_keys(registry):
    keys = _keystore_with()  # empty
    with pytest.raises(ProviderUnavailableError):
        await registry.resolve(Task.WRITE_SCRIPT, "claude", _ctx(keys))


async def test_keyless_provider_always_available(registry):
    # No keys at all, but edge is keyless -> resolves for voice.
    keys = _keystore_with()
    client = await registry.resolve(Task.GENERATE_VOICE, "edge", _ctx(keys))
    assert client.provider_id == "edge"


async def test_voice_fallback_to_keyless_when_paid_key_missing(registry):
    # Prefer eleven (no key) -> fall back to keyless edge.
    keys = _keystore_with()
    client = await registry.resolve(Task.GENERATE_VOICE, "eleven", _ctx(keys))
    assert client.provider_id == "edge"


def test_for_capability_and_key_ref_mapping(registry):
    script = {c.provider_id for c in registry.for_capability(Task.WRITE_SCRIPT)}
    assert {"claude", "chatgpt", "gemini", "down"} <= script
    assert registry.key_ref_for_provider("eleven") == "elevenlabs"
    assert registry.provider_for_key_ref("elevenlabs") == "eleven"
    assert registry.key_ref_for_provider("edge") is None  # keyless


def test_default_registry_loads_real_catalog():
    """The bundled services.yaml imports every real client class cleanly."""
    reg = get_registry()
    assert "edge" in reg.services_raw
    assert reg.try_get("stub-script") is not None
