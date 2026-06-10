"""SeriesSpec ↔ ORM mapping + persistence helpers (module-1 §5/§6/§11).

Keeps ``series.spec_json`` (JSONB) as the **source of truth** for the full
:class:`models.spec.SeriesSpec`, and mirrors each episode into the ``episodes``
table so that ``GET /episodes/{id}/script`` and Module 2 can look an episode up
by id and track its ``status`` without parsing JSONB.

These helpers compose the platform-lead's repositories (``SeriesRepo`` /
``EpisodeRepo``) with a few direct session adds for episode-row creation — the
repository *shapes* are untouched (contract held).

UI mapping (data.ts):
- :func:`series_to_ui` → ``Series`` (+ ``Episode[]``, ``ScriptSegment[]`` via the
  episode's ``segments``). ``cover`` is derived from the first segment's label.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Episode, Series
from db.repository import EpisodeRepo, SeriesRepo
from models.spec import EpisodeSpec, SeriesSpec


def spec_from_row(row: Series) -> SeriesSpec:
    """Reconstruct a :class:`SeriesSpec` from a ``series`` row's ``spec_json``."""
    return SeriesSpec.model_validate(row.spec_json)


def find_episode_in_spec(spec: SeriesSpec, episode_id: str) -> EpisodeSpec | None:
    for ep in spec.episodes:
        if ep.episode_id == episode_id:
            return ep
    return None


async def save_series_spec(session: AsyncSession, user_id: str, spec: SeriesSpec) -> Series:
    """Persist a full :class:`SeriesSpec` (approve / update) + mirror episode rows.

    Upserts the ``series`` row (``spec_json``) and ensures one ``episodes`` row per
    spec episode, syncing ``title`` / ``order`` / ``status``. The full spec lives
    in ``spec_json``; the episode rows are the lookup + status mirror.
    """
    series_repo = SeriesRepo(session)
    row = await series_repo.upsert(
        user_id=user_id,
        series_id=spec.series_id,
        name=spec.name,
        topic=spec.topic,
        skill=spec.skill,
        spec_json=spec.model_dump(),
    )
    ep_repo = EpisodeRepo(session)
    existing = {e.id: e for e in await ep_repo.list_for_series(user_id, spec.series_id)}
    for ep in spec.episodes:
        orm = existing.get(ep.episode_id)
        if orm is None:
            session.add(
                Episode(
                    id=ep.episode_id,
                    series_id=spec.series_id,
                    user_id=user_id,
                    title=ep.title,
                    order=ep.order,
                    status=ep.status,
                )
            )
        else:
            orm.title = ep.title
            orm.order = ep.order
            orm.status = ep.status
    await session.flush()
    return row


async def update_episode_in_series(
    session: AsyncSession, user_id: str, series_id: str, updated: EpisodeSpec
) -> SeriesSpec:
    """Write an updated :class:`EpisodeSpec` back into its series + mirror status.

    Used by the lazy-script path: replaces the matching episode inside
    ``spec_json`` and updates the ``episodes`` row's ``status``.
    """
    series_repo = SeriesRepo(session)
    row = await series_repo.get(user_id, series_id)
    if row is None:
        raise KeyError(f"series {series_id} not found for user")
    spec = spec_from_row(row)
    spec.episodes = [updated if e.episode_id == updated.episode_id else e for e in spec.episodes]
    row.spec_json = spec.model_dump()
    await EpisodeRepo(session).set_status(user_id, updated.episode_id, updated.status)
    await session.flush()
    return spec


async def reset_episode_to_outline(
    session: AsyncSession, user_id: str, series_id: str, episode_id: str
) -> EpisodeSpec | None:
    """Strip an episode back to outline-only inside its series ``spec_json``.

    Clears ``segments`` (the written script) and the lazy ``youtube`` meta but
    KEEPS the outline identity the wizard produced: ``title`` / ``order`` /
    ``desc`` / ``target_minutes`` (so "làm lại từ đầu" re-scripts the same episode,
    not a blank one). Resets ``status`` to ``draft`` and mirrors that onto the
    episode row. Returns the reset :class:`EpisodeSpec`, or ``None`` if the series
    or episode is missing. The caller deletes assets / gen_jobs separately.
    """
    row = await SeriesRepo(session).get(user_id, series_id)
    if row is None:
        return None
    spec = spec_from_row(row)
    target = find_episode_in_spec(spec, episode_id)
    if target is None:
        return None
    reset = EpisodeSpec(
        episode_id=target.episode_id,
        title=target.title,
        order=target.order,
        desc=target.desc,
        target_minutes=target.target_minutes,
        status="draft",
        youtube=None,
        segments=[],
    )
    spec.episodes = [reset if e.episode_id == episode_id else e for e in spec.episodes]
    row.spec_json = spec.model_dump()
    await EpisodeRepo(session).set_status(user_id, episode_id, "draft")
    await session.flush()
    return reset


async def find_series_for_episode(
    session: AsyncSession, user_id: str, episode_id: str
) -> tuple[Series, SeriesSpec, EpisodeSpec] | None:
    """Locate the series + spec + episode for an ``episode_id`` (worker entrypoint).

    Resolves via the ``episodes`` row (→ ``series_id``) then loads the spec.
    """
    ep_row = await EpisodeRepo(session).get(user_id, episode_id)
    if ep_row is None:
        return None
    row = await SeriesRepo(session).get(user_id, ep_row.series_id)
    if row is None:
        return None
    spec = spec_from_row(row)
    ep = find_episode_in_spec(spec, episode_id)
    if ep is None:
        return None
    return row, spec, ep


# --------------------------------------------------------------------------- #
# UI projection (reelo-ui/lib/data.ts)                                        #
# --------------------------------------------------------------------------- #
def episode_to_ui(ep: EpisodeSpec) -> dict[str, Any]:
    """Project an :class:`EpisodeSpec` onto the UI ``Episode`` shape."""
    return {"id": ep.episode_id, "title": ep.title, "status": ep.status}


def segments_to_ui(ep: EpisodeSpec) -> list[dict[str, str]]:
    """Project ``segments`` onto UI ``ScriptSegment[]`` ``{id, text, img}``."""
    return [
        {"id": f"seg{seg.index}", "text": seg.narration, "img": seg.image_prompt}
        for seg in ep.segments
    ]


def series_to_ui(spec: SeriesSpec) -> dict[str, Any]:
    """Project a :class:`SeriesSpec` onto the UI ``Series`` shape (data.ts)."""
    cover = ""
    for ep in spec.episodes:
        if ep.segments:
            cover = ep.segments[0].image_prompt
            break
    return {
        "id": spec.series_id,
        "name": spec.name,
        "topic": spec.topic,
        "skill": spec.skill,
        "providers": spec.providers,
        "cover": cover,
        "episodes": [episode_to_ui(ep) for ep in spec.episodes],
    }


__all__ = [
    "spec_from_row",
    "find_episode_in_spec",
    "save_series_spec",
    "update_episode_in_series",
    "reset_episode_to_outline",
    "find_series_for_episode",
    "episode_to_ui",
    "segments_to_ui",
    "series_to_ui",
]
