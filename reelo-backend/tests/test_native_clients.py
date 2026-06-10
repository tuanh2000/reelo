"""Native HTTP/SDK clients (gemini / openai-compat / claude) — mocked, no network.

Covers: missing-key -> InvalidKeyError before any SDK call, usage mapping, and
the JSON-mode request shape. The provider SDK objects are stubbed so nothing
hits the wire.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clients.anthropic import ClaudeClient, _extract_text, _map_anthropic_usage
from clients.base import CallContext, InvalidKeyError, ScriptRequest, ServiceConfig
from clients.gemini import GeminiClient, _map_gemini_usage, _messages_to_gemini
from clients.openai_compat import OpenAIStyleClient, _map_openai_usage, _normalize_size
from keystore import Cipher, KeyStore
from usage import UsageLogger


def _ctx(*refs: str) -> CallContext:
    store = KeyStore(Cipher(b"k" * 32))
    for r in refs:
        store.save("u1", r, f"sk-{r}")
    return CallContext(user_id="u1", keys=store, usage=UsageLogger())


_GEMINI_CFG = ServiceConfig(
    provider_id="gemini",
    raw={
        "auth": {"type": "key", "key_ref": "google_aistudio"},
        "tasks": {"write-script": {"default_model": "gemini-2.5-flash"}},
        "pricing": {"write-script": {"per_1k_input": 0.0, "per_1k_output": 0.0}},
    },
)
_OPENAI_CFG = ServiceConfig(
    provider_id="chatgpt",
    raw={
        "auth": {"type": "key", "key_ref": "openai"},
        "tasks": {"write-script": {"default_model": "gpt-4o"}},
        "pricing": {"write-script": {"per_1k_input": 0.0025, "per_1k_output": 0.01}},
    },
)
_CLAUDE_CFG = ServiceConfig(
    provider_id="claude",
    raw={
        "auth": {"type": "key", "key_ref": "anthropic"},
        "tasks": {"write-script": {"default_model": "claude-sonnet-4-6"}},
        "pricing": {"write-script": {"per_1k_input": 0.003, "per_1k_output": 0.015}},
    },
)


# ---- missing-key guards (no SDK touched) ---------------------------------- #
async def test_gemini_missing_key_raises():
    with pytest.raises(InvalidKeyError):
        await GeminiClient(_GEMINI_CFG).write_script(ScriptRequest(messages=[]), _ctx())


async def test_openai_missing_key_raises():
    with pytest.raises(InvalidKeyError):
        await OpenAIStyleClient(_OPENAI_CFG).write_script(ScriptRequest(messages=[]), _ctx())


async def test_claude_missing_key_raises():
    with pytest.raises(InvalidKeyError):
        await ClaudeClient(_CLAUDE_CFG).write_script(ScriptRequest(messages=[]), _ctx())


# ---- usage mapping helpers ------------------------------------------------- #
def test_map_gemini_usage():
    resp = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=10, candidates_token_count=20, total_token_count=30
        )
    )
    assert _map_gemini_usage(resp) == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
    }


def test_map_openai_usage():
    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=7, total_tokens=12)
    )
    assert _map_openai_usage(resp)["total_tokens"] == 12


def test_map_anthropic_usage():
    resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=8, output_tokens=4))
    u = _map_anthropic_usage(resp)
    assert u["total_tokens"] == 12


# ---- claude tool-call JSON extraction ------------------------------------- #
def test_claude_extracts_tool_input_as_json():
    block = SimpleNamespace(type="tool_use", input={"segments": [1, 2]})
    resp = SimpleNamespace(content=[block])
    text = _extract_text(resp, want_json=True)
    assert json.loads(text) == {"segments": [1, 2]}


def test_claude_falls_back_to_text_block():
    block = SimpleNamespace(type="text", text="plain answer")
    resp = SimpleNamespace(content=[block])
    assert _extract_text(resp, want_json=False) == "plain answer"


# ---- helper edge cases ----------------------------------------------------- #
def test_openai_size_normalization():
    block = {"sizes": ["1024x1024", "1792x1024"]}
    assert _normalize_size("16:9", block) == "1792x1024"
    assert _normalize_size("1024x1024", block) == "1024x1024"  # already pixels
    assert _normalize_size("weird", block) == "1024x1024"  # first listed


def test_gemini_message_role_mapping():
    from google.genai import types

    msgs = _messages_to_gemini(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}], types
    )
    assert msgs[0].role == "user"
    assert msgs[1].role == "model"


# ---- write_script dispatch with a stubbed SDK client ----------------------- #
async def test_openai_write_script_dispatch(monkeypatch):
    """Patch AsyncOpenAI so write_script runs end-to-end without network."""

    class FakeChatCompletions:
        async def create(self, **kwargs):
            FakeChatCompletions.last_kwargs = kwargs
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7),
            )

    class FakeClient:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=FakeChatCompletions())

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeClient)

    ctx = _ctx("openai")
    client = OpenAIStyleClient(_OPENAI_CFG)
    req = ScriptRequest(
        messages=[{"role": "user", "content": "write"}],
        system="be terse",
        json_schema={"type": "object"},
    )
    res = await client.write_script(req, ctx)
    assert json.loads(res.text) == {"ok": True}
    assert res.usage["total_tokens"] == 7
    # system prompt + json_schema response_format wired through
    kw = FakeChatCompletions.last_kwargs
    assert kw["messages"][0] == {"role": "system", "content": "be terse"}
    assert kw["response_format"]["type"] == "json_schema"
    # usage recorded
    assert ctx.usage._sink.events[0].units == 7  # type: ignore[attr-defined]
