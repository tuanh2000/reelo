"""Thumbnail generation (`thumbnail.py`, M2-4).

After render, generate **3 candidates** via the image client (same provider as the
episode's images) using an auto-built prompt derived from the episode title +
series ``image_style`` (preset base_prompt + palette + style_layer). The three
prompts vary the composition direction (bold close-up / wide establishing /
symbolic) so the user has distinct choices at Review; they pick one
(``PublishMeta.thumbnailIndex``). Files land in ``thumbnails/thumb_{1..3}.png``.

v1 uses a simple prompt template (Module 2 §7 / §16 open question #6); it can be
upgraded toward the watercolor/typography skill thumbnail style later.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from clients.base import CallContext, ImageRequest, Task
from clients.registry import ServiceRegistry, get_registry
from models.spec import SeriesSpec

from module2.materialize import ProjectLayout

log = logging.getLogger("reelo.module2.thumbnail")

THUMB_COUNT = 3

# Composition directions, one per candidate, blended into the prompt.
_VARIATIONS: list[str] = [
    "bold dramatic close-up, high contrast, eye-catching focal subject, "
    "YouTube thumbnail composition with strong negative space for a title",
    "wide cinematic establishing shot, atmospheric depth, rule-of-thirds framing, "
    "clear focal point, room for overlay text",
    "symbolic minimalist composition, single iconic element, clean background, "
    "striking and memorable, leaves space for large title text",
]


def build_thumbnail_prompts(series: SeriesSpec, title: str) -> list[str]:
    """Compose ``THUMB_COUNT`` distinct English thumbnail prompts.

    Each prompt = preset ``base_prompt`` + skill ``style_layer`` + palette hint +
    the episode subject (title) + a per-candidate composition direction.
    """
    style = series.image_style
    palette = ", ".join(style.palette) if style.palette else ""
    base_parts = [style.base_prompt, style.style_layer]
    base = ". ".join(p.strip() for p in base_parts if p and p.strip())
    palette_hint = f"Color palette: {palette}." if palette else ""
    subject = f'YouTube thumbnail for "{title}".'

    prompts: list[str] = []
    for variation in _VARIATIONS:
        prompt = ". ".join(
            part.strip().rstrip(".")
            for part in (subject, base, palette_hint, variation)
            if part and part.strip()
        )
        prompts.append(prompt + ".")
    return prompts


async def generate_thumbnails(
    series: SeriesSpec,
    title: str,
    lo: ProjectLayout,
    ctx: CallContext,
    *,
    registry: ServiceRegistry | None = None,
    concurrency: int = 3,
) -> list[Path]:
    """Generate 3 thumbnail PNGs into ``thumbnails/`` via the image client.

    Failures are tolerated per-candidate (thumbnails are non-blocking, unlike
    segment images): a candidate that errors is skipped and logged. Returns the
    paths actually written, in order.

    Args:
        series: provides the image provider + style/palette.
        title: episode title (the thumbnail subject).
        lo: project layout (writes under ``thumbnails/``).
        ctx: per-user call context.
        registry: override the process registry (tests).
        concurrency: max parallel image calls.
    """
    reg = registry or get_registry()
    provider = series.providers.get("image", "kie")
    client = await reg.resolve(Task.GENERATE_IMAGE, provider, ctx)

    prompts = build_thumbnail_prompts(series, title)
    sem = asyncio.Semaphore(max(1, concurrency))
    lo.thumbnails_dir.mkdir(parents=True, exist_ok=True)

    async def _one(i: int, prompt: str) -> Path | None:
        out = lo.thumbnails_dir / f"thumb_{i}.png"
        async with sem:
            try:
                await client.generate_image(
                    ImageRequest(prompt=prompt, size=series.image_style.aspect),
                    out_path=out,
                    ctx=ctx,
                )
                return out
            except Exception as exc:  # noqa: BLE001 — thumbnails are best-effort
                log.warning("thumbnail %d failed: %s", i, exc)
                return None

    results = await asyncio.gather(
        *[_one(i, p) for i, p in enumerate(prompts, start=1)]
    )
    return [p for p in results if p is not None]


__all__ = ["THUMB_COUNT", "build_thumbnail_prompts", "generate_thumbnails"]
