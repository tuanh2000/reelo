"""generate_episode_script: full lazy gen (chunked), retry policy, reindex (§7-9).

Uses test-local fake clients (a clean one honouring idx_start/count, and a flaky
one that fails twice then succeeds) plus the EchoScriptClient stub for the YouTube
metadata call. No real API.
"""

from __future__ import annotations

import json

import pytest

from clients.base import (
    AIClient,
    CallContext,
    ScriptRequest,
    ScriptResult,
    ServiceConfig,
    Task,
)
from clients.registry import ServiceRegistry
from keystore import Cipher, KeyStore
from models.spec import EpisodeSpec, ImageStyle, SeriesSpec, VoiceConfig
from module1.episode_script import (
    ScriptCancelled,
    ScriptGenerationError,
    generate_episode_script,
    reindex,
)
from usage import UsageLogger


def _ctx() -> CallContext:
    return CallContext(user_id="u1", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())


def _series(target=5, density="standard") -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="Ancient Faiths", topic="religion", skill="religion",
        language="vi", target_minutes=target, density=density,
        providers={"script": "fake", "image": "kie", "voice": "edge"},
        image_style=ImageStyle(preset_id="painterly-devotional", base_prompt="b"),
        voice=VoiceConfig(provider="edge", voice_id="v"),
        episodes=[EpisodeSpec(episode_id="e1", title="Origins", order=1, desc="the start")],
    )


def _parse_count_and_start(req: ScriptRequest) -> tuple[int, int]:
    """Read the requested count + idx_start out of the user instruction."""
    text = req.messages[0]["content"]
    # "Write segments index A..B (N segment(s))"
    import re

    m = re.search(r"index (\d+)\.\.(\d+)", text)
    a, b = int(m.group(1)), int(m.group(2))
    return (b - a + 1), a


def _segments_json(count: int, start: int) -> str:
    segs = [
        {
            "index": start + i,
            "narration": f"Doan {start + i}.",
            "image_prompt": f"a reverent scene number {start + i}",
            "image_label": f"scene-{start + i}",
        }
        for i in range(count)
    ]
    return json.dumps({"segments": segs}, ensure_ascii=False)


class FakeSegmentClient(AIClient):
    """Returns exactly the requested chunk; YouTube call returns valid metadata."""

    capabilities = {Task.WRITE_SCRIPT}
    cost_tier = "free"
    requires_key = False

    async def is_available(self, ctx):
        return True

    async def write_script(self, req: ScriptRequest, ctx) -> ScriptResult:
        ctx.usage.record(ctx.user_id, self.provider_id, Task.WRITE_SCRIPT.value, 1.0, 0.0)
        # YouTube metadata request? (its schema has a 'title' top-level prop)
        props = (req.json_schema or {}).get("properties", {})
        if "title" in props and "segments" not in props:
            return ScriptResult(
                text=json.dumps({"title": "Tieu de", "description": "Mo ta", "tags": ["a", "b"]})
            )
        count, start = _parse_count_and_start(req)
        return ScriptResult(text=_segments_json(count, start))


class FlakyClient(FakeSegmentClient):
    """Fails parse twice (per chunk-start), then returns a valid chunk on attempt 3."""

    def __init__(self, config):
        super().__init__(config)
        self._attempts: dict[int, int] = {}

    async def write_script(self, req: ScriptRequest, ctx) -> ScriptResult:
        props = (req.json_schema or {}).get("properties", {})
        if "title" in props and "segments" not in props:
            return ScriptResult(text=json.dumps({"title": "T", "description": "D", "tags": []}))
        count, start = _parse_count_and_start(req)
        self._attempts[start] = self._attempts.get(start, 0) + 1
        if self._attempts[start] < 3:
            return ScriptResult(text="sorry, here is some prose with no json")
        return ScriptResult(text=_segments_json(count, start))


class AlwaysBadClient(FakeSegmentClient):
    async def write_script(self, req, ctx) -> ScriptResult:
        props = (req.json_schema or {}).get("properties", {})
        if "title" in props and "segments" not in props:
            return ScriptResult(text=json.dumps({"title": "T", "description": "D", "tags": []}))
        return ScriptResult(text="never valid json")


class UnavailableClient(FakeSegmentClient):
    async def is_available(self, ctx):
        return False


def _registry_with(client_cls) -> ServiceRegistry:
    reg = ServiceRegistry.__new__(ServiceRegistry)
    reg._raw = {}
    reg._fallback = {}
    cfg = ServiceConfig(provider_id="fake", raw={"auth": {"type": "none"}})
    reg._clients = {"fake": client_cls(cfg)}
    return reg


async def test_generate_episode_script_happy_path():
    series = _series(target=5, density="standard")  # → 9 segments
    ep = series.episodes[0]
    out = await generate_episode_script(series, ep, _ctx(), registry=_registry_with(FakeSegmentClient))
    assert out.status == "scripted"
    assert len(out.segments) == 9
    assert [s.index for s in out.segments] == list(range(1, 10))
    assert out.youtube == {"title": "Tieu de", "description": "Mo ta", "tags": ["a", "b"]}
    # image_prompt stays English-ish; narration in the series language; labels unique
    assert len({s.image_label for s in out.segments}) == 9


async def test_generate_episode_script_long_video_many_chunks():
    series = _series(target=25, density="dense")  # → 68 segments, many chunks
    out = await generate_episode_script(
        series, series.episodes[0], _ctx(), registry=_registry_with(FakeSegmentClient)
    )
    assert len(out.segments) == 68
    assert [s.index for s in out.segments] == list(range(1, 69))


async def test_retry_recovers_after_two_bad_responses():
    series = _series(target=5, density="standard")
    out = await generate_episode_script(
        series, series.episodes[0], _ctx(), registry=_registry_with(FlakyClient)
    )
    assert out.status == "scripted"
    assert len(out.segments) == 9


async def test_retry_budget_exhausted_raises():
    series = _series(target=5, density="standard")
    with pytest.raises(ScriptGenerationError):
        await generate_episode_script(
            series, series.episodes[0], _ctx(), registry=_registry_with(AlwaysBadClient)
        )


async def test_idempotent_when_already_scripted():
    series = _series()
    from models.spec import SegmentSpec

    ep = series.episodes[0].model_copy(
        update={
            "status": "scripted",
            "segments": [SegmentSpec(index=1, narration="x", image_prompt="y", image_label="z")],
        }
    )
    out = await generate_episode_script(series, ep, _ctx(), registry=_registry_with(AlwaysBadClient))
    assert out is ep  # untouched, no AI call


async def test_provider_unavailable_bubbles_not_counted_as_parse_retry():
    from clients.base import ProviderUnavailableError

    series = _series()
    with pytest.raises(ProviderUnavailableError):
        await generate_episode_script(
            series, series.episodes[0], _ctx(), registry=_registry_with(UnavailableClient)
        )


def _explain_series(target=5, density="standard") -> SeriesSpec:
    """A general-topic series using the explain skill (no religion)."""
    return SeriesSpec(
        series_id="s2", name="Vanishing Wild", topic="endangered animals", skill="explain",
        language="en", target_minutes=target, density=density,
        providers={"script": "fake", "image": "web", "voice": "edge"},
        image_style=ImageStyle(preset_id="documentary", base_prompt="b"),
        voice=VoiceConfig(provider="edge", voice_id="v"),
        episodes=[
            EpisodeSpec(
                episode_id="e1", title="The Vaquita", order=1,
                desc="the world's rarest marine mammal",
            )
        ],
    )


class CapturingClient(FakeSegmentClient):
    """Records the system prompts it is asked to write against (no refusal)."""

    def __init__(self, config):
        super().__init__(config)
        self.systems: list[str] = []

    async def write_script(self, req: ScriptRequest, ctx) -> ScriptResult:
        self.systems.append(req.system or "")
        return await super().write_script(req, ctx)


async def test_general_topic_explain_skill_generates_without_refusal():
    """A non-religious topic (endangered animals) with the explain skill produces
    a full script. The chunk system prompt carries the explainer style and the
    general topic — and never the religion content gate."""
    series = _explain_series(target=5, density="standard")  # → 9 segments
    reg = _registry_with(CapturingClient)
    client = reg._clients["fake"]
    out = await generate_episode_script(series, series.episodes[0], _ctx(), registry=reg)

    assert out.status == "scripted"
    assert len(out.segments) == 9
    # The chunk system prompt is the explainer style applied to this topic — and
    # contains no genre restriction / religion rule.
    script_systems = [s for s in client.systems if "scriptwriter" in s]
    assert script_systems
    sys = script_systems[0]
    assert "Explainer" in sys
    assert "NO subject restriction" in sys
    assert "three-layer method" not in sys
    assert "ALREADY a believer" not in sys


class CountingClient(FakeSegmentClient):
    """Counts the chunk (token-spending) calls so a cancel can be shown to bound them."""

    def __init__(self, config):
        super().__init__(config)
        self.chunk_calls = 0

    async def write_script(self, req: ScriptRequest, ctx) -> ScriptResult:
        props = (req.json_schema or {}).get("properties", {})
        is_chunk = not ("title" in props and "segments" not in props)
        if is_chunk:
            self.chunk_calls += 1
        return await super().write_script(req, ctx)


async def test_cancel_before_first_call_raises_and_spends_no_tokens():
    """A stop requested before generation starts raises ScriptCancelled without
    making a single model call (the whole point: stop burning tokens)."""
    series = _series(target=5, density="standard")
    reg = _registry_with(CountingClient)
    client = reg._clients["fake"]

    async def always_cancel() -> bool:
        return True

    with pytest.raises(ScriptCancelled):
        await generate_episode_script(
            series, series.episodes[0], _ctx(), registry=reg, should_cancel=always_cancel
        )
    assert client.chunk_calls == 0


async def test_cancel_after_first_chunk_stops_early():
    """Cancelling mid-run stops BEFORE the next chunk's call — so a long, many-chunk
    script makes one call instead of dozens once the stop is requested."""
    series = _series(target=25, density="dense")  # → 68 segments, many chunks
    reg = _registry_with(CountingClient)
    client = reg._clients["fake"]

    async def cancel_after_one() -> bool:
        return client.chunk_calls >= 1

    with pytest.raises(ScriptCancelled):
        await generate_episode_script(
            series, series.episodes[0], _ctx(), registry=reg, should_cancel=cancel_after_one
        )
    # One chunk call got through, then it stopped — nowhere near the full run.
    assert client.chunk_calls == 1


async def test_should_cancel_none_is_unchanged_behaviour():
    """The default (no should_cancel) path is identical to before — a full script."""
    series = _series(target=5, density="standard")  # → 9 segments
    out = await generate_episode_script(
        series, series.episodes[0], _ctx(), registry=_registry_with(FakeSegmentClient)
    )
    assert out.status == "scripted"
    assert len(out.segments) == 9


def test_reindex_renumbers_and_dedupes_labels():
    from models.spec import SegmentSpec

    segs = [
        SegmentSpec(index=5, narration="a", image_prompt="p1", image_label="dup"),
        SegmentSpec(index=9, narration="b", image_prompt="p2", image_label="dup"),
        SegmentSpec(index=2, narration="c", image_prompt="a quiet hill", image_label=""),
    ]
    out = reindex(segs)
    assert [s.index for s in out] == [1, 2, 3]
    assert len({s.image_label for s in out}) == 3  # deduped
    assert out[2].image_label == "a-quiet-hill"  # empty → slug from prompt
