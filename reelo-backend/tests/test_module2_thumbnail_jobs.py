"""Thumbnail generation (stub-image) + job seeding/cost estimate."""

from __future__ import annotations

from clients.base import CallContext, ServiceConfig
from clients.stub import PlaceholderImageClient
from keystore import Cipher, KeyStore
from models.spec import (
    EpisodeSpec,
    ImageStyle,
    SegmentSpec,
    SeriesSpec,
    VoiceConfig,
)
from module2 import jobs as jobmod
from module2 import thumbnail
from module2.materialize import layout_for
from usage import UsageLogger


def _series() -> SeriesSpec:
    return SeriesSpec(
        series_id="s1", name="Faiths", topic="religion", skill="religion",
        language="vi", target_minutes=5, density="standard",
        providers={"script": "stub-script", "image": "stub-image", "voice": "stub-voice"},
        image_style=ImageStyle(
            preset_id="p", base_prompt="oil painting", style_layer="christian",
            palette=["#112233", "#445566"], aspect="16:9",
        ),
        voice=VoiceConfig(provider="stub-voice", voice_id="v"),
    )


def _episode(n: int = 4) -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="e1", title="The First Temple", order=1, status="scripted",
        segments=[
            SegmentSpec(index=i, narration="word " * 20, image_prompt="x", image_label=f"l{i}")
            for i in range(1, n + 1)
        ],
    )


class _Registry:
    def __init__(self, client):
        self._c = client

    async def resolve(self, task, preferred, ctx):
        return self._c


# --------------------------------------------------------------------------- #
# Thumbnail prompts + generation                                             #
# --------------------------------------------------------------------------- #
def test_build_thumbnail_prompts_three_distinct():
    prompts = thumbnail.build_thumbnail_prompts(_series(), "The First Temple")
    assert len(prompts) == thumbnail.THUMB_COUNT == 3
    assert len(set(prompts)) == 3  # distinct compositions
    for p in prompts:
        assert "The First Temple" in p
        assert "oil painting" in p
        assert "#112233" in p  # palette injected


async def test_generate_thumbnails_writes_three(tmp_path):
    lo = layout_for(tmp_path)
    lo.thumbnails_dir.mkdir(parents=True)
    client = PlaceholderImageClient(ServiceConfig("stub-image", {}))
    ctx = CallContext(user_id="u", keys=KeyStore(Cipher(b"k" * 32)), usage=UsageLogger())
    paths = await thumbnail.generate_thumbnails(
        _series(), "Title", lo, ctx, registry=_Registry(client)
    )
    assert len(paths) == 3
    for i, p in enumerate(paths, start=1):
        assert p.name == f"thumb_{i}.png"
        assert p.exists() and p.read_bytes().startswith(b"\x89PNG")


# --------------------------------------------------------------------------- #
# Cost estimate                                                               #
# --------------------------------------------------------------------------- #
def test_cost_estimate_scripted_uses_segment_counts():
    est = jobmod.cost_estimate(_series(), _episode(4))
    assert est.images == 4
    assert est.voice_chars > 0
    # stub-image / stub-voice priced at 0 -> free
    assert est.estimated_cost is None
    assert "free" in (est.note or "")


def test_cost_estimate_unscripted_derives_counts():
    ep = EpisodeSpec(episode_id="e9", title="x", order=1)  # no segments
    est = jobmod.cost_estimate(_series(), ep)
    assert est.images > 0  # derived from target_minutes x density
    assert est.voice_chars > 0


def test_cost_estimate_paid_providers_nonzero():
    series = _series()
    series.providers["image"] = "kie"  # 0.0 by default; switch voice to eleven
    series.voice.provider = "eleven"
    est = jobmod.cost_estimate(series, _episode(4))
    # eleven priced at 0.30 / 1k chars -> nonzero
    assert est.estimated_cost is not None and est.estimated_cost > 0
