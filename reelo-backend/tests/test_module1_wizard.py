"""Wizard logic: message assembly, outline parse (non-fatal), approve shell (§3/§4/§6)."""

from __future__ import annotations

import pytest

from clients.base import AIClient, CallContext, ScriptRequest, ScriptResult, ServiceConfig, Task
from clients.registry import ServiceRegistry
from keystore import Cipher, KeyStore
from models.spec import ImageStyle, VoiceConfig
from module1.wizard import (
    build_messages,
    build_series_spec,
    parse_outline_preview,
    run_phase_a,
)
from usage import UsageLogger


# --- Phase A helpers -------------------------------------------------------
def test_build_messages_maps_roles_and_appends_idea():
    history = [
        {"role": "user", "text": "an idea about rome"},
        {"role": "ai", "text": "here is an outline"},
    ]
    msgs = build_messages("make it more dramatic", history)
    assert msgs == [
        {"role": "user", "content": "an idea about rome"},
        {"role": "assistant", "content": "here is an outline"},
        {"role": "user", "content": "make it more dramatic"},
    ]


def test_build_messages_skips_empty():
    assert build_messages("", [{"role": "user", "text": ""}]) == []


def test_parse_outline_block():
    reply = (
        "Great idea! Here's a draft.\n"
        "<<<OUTLINE>>>\n"
        "1 | The Origins | how the gods began\n"
        "2 | Mesopotamia | polytheism in the city\n"
        "<<<END_OUTLINE>>>\n"
        "Want changes?"
    )
    out = parse_outline_preview(reply)
    assert out is not None and len(out) == 2
    assert out[0].title == "The Origins"
    assert out[0].desc == "how the gods began"
    assert out[0].pick is True


def test_parse_outline_missing_is_none():
    assert parse_outline_preview("Just a friendly reply, no block.") is None


def test_parse_outline_garbled_is_none():
    reply = "<<<OUTLINE>>>\nnot a valid row\n<<<END_OUTLINE>>>"
    assert parse_outline_preview(reply) is None


def test_parse_outline_title_only():
    reply = "<<<OUTLINE>>>\n1 | A title with no desc\n<<<END_OUTLINE>>>"
    out = parse_outline_preview(reply)
    assert out and out[0].title == "A title with no desc" and out[0].desc == ""


def test_parse_outline_unclosed_block_still_parses():
    reply = "<<<OUTLINE>>>\n1 | Title | desc"  # no closing marker
    out = parse_outline_preview(reply)
    assert out and out[0].title == "Title"


# --- Phase A run (topic-agnostic) -----------------------------------------
class _CapturingClient(AIClient):
    """Records the system prompt and echoes the latest user turn back."""

    capabilities = {Task.WRITE_SCRIPT}
    cost_tier = "free"
    requires_key = False

    def __init__(self, config):
        super().__init__(config)
        self.last_system: str | None = None

    async def is_available(self, ctx):
        return True

    async def write_script(self, req: ScriptRequest, ctx) -> ScriptResult:
        self.last_system = req.system
        last = req.messages[-1]["content"] if req.messages else ""
        return ScriptResult(text=f"echo: {last}")


def _registry_with_capture() -> ServiceRegistry:
    reg = ServiceRegistry.__new__(ServiceRegistry)
    reg._raw = {}
    reg._fallback = {}
    cfg = ServiceConfig(provider_id="cap", raw={"auth": {"type": "none"}})
    reg._clients = {"cap": _CapturingClient(cfg)}
    return reg


def _ctx() -> CallContext:
    return CallContext(user_id="u1", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())


@pytest.mark.parametrize("skill", ["explain", "religion", "story", "news"])
async def test_run_phase_a_is_topic_agnostic_regardless_of_skill(skill):
    """Phase A never refuses a topic and never leaks a skill's content gate —
    the user's "endangered animals" request must be accepted for ANY skill."""
    reg = _registry_with_capture()
    client = reg._clients["cap"]
    result = await run_phase_a(
        "a video series about endangered animals",
        [],
        skill=skill,
        provider="cap",
        ctx=_ctx(),
        registry=reg,
    )
    assert result.reply  # got a reply, no refusal
    sys = client.last_system or ""
    assert "ANY topic" in sys
    assert "Never refuse" in sys
    # The religion skill's scholarly gate must never reach the chat system prompt.
    assert "three-layer method" not in sys
    assert "ALREADY a believer" not in sys


# --- Phase B approve (no AI) ----------------------------------------------
def _voice():
    return VoiceConfig(provider="edge", voice_id="v")


def _style():
    return ImageStyle(preset_id="painterly-devotional", base_prompt="base")


def test_build_series_spec_picks_only_selected():
    spec = build_series_spec(
        name="Faiths", topic="religion",
        outline=[
            {"title": "Ep A", "desc": "da", "pick": True},
            {"title": "Ep B", "desc": "db", "pick": False},
            {"title": "Ep C", "desc": "dc", "pick": True},
        ],
        skill="religion", language="vi", target_minutes=12, density="dense",
        providers={"script": "claude", "image": "kie", "voice": "edge"},
        voice=_voice(), image_style=_style(),
    )
    assert [e.title for e in spec.episodes] == ["Ep A", "Ep C"]
    assert [e.order for e in spec.episodes] == [1, 2]
    # shells: empty segments, draft, no youtube, inherits target via None
    for e in spec.episodes:
        assert e.segments == []
        assert e.status == "draft"
        assert e.youtube is None
        assert e.target_minutes is None
    assert spec.target_minutes == 12
    assert spec.density == "dense"
    assert spec.image_style.preset_id == "painterly-devotional"


def test_build_series_spec_defaults_pick_true():
    spec = build_series_spec(
        name="N", topic="t", outline=[{"title": "X", "desc": ""}],
        skill="story", language="en", target_minutes=10, density="standard",
        providers={"script": "chatgpt", "image": "kie", "voice": "edge"},
        voice=_voice(), image_style=_style(),
    )
    assert len(spec.episodes) == 1  # missing pick → treated as picked


def test_build_series_spec_round_trips_to_jsonb():
    from models.spec import SeriesSpec

    spec = build_series_spec(
        name="N", topic="t", outline=[{"title": "X", "desc": "d", "pick": True}],
        skill="religion", language="vi", target_minutes=10, density="standard",
        providers={"script": "stub-script", "image": "kie", "voice": "edge"},
        voice=_voice(), image_style=_style(),
    )
    restored = SeriesSpec.model_validate(spec.model_dump())
    assert restored == spec
