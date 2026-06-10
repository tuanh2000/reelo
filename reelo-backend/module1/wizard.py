"""Wizard logic — Phase A refine (chat) + Phase B approve (build SeriesSpec).

Phase A (``run_phase_a``) — stateless (D9): assemble ``messages[]`` from the
UI-supplied history + the latest idea, call ``registry.resolve(WRITE_SCRIPT)``,
return the model reply plus a best-effort outline preview parsed from the
``<<<OUTLINE>>>`` block. Parsing failure is **non-fatal** (§4): the reply is
returned and the outline left ``None``.

Phase B (``build_series_spec``) — NO AI (D6): take the user-edited outline +
config and assemble a :class:`SeriesSpec` shell (episodes with empty segments,
status ``draft``). The router persists it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from clients.base import CallContext, ScriptRequest, Task
from clients.registry import ServiceRegistry, get_registry
from models.spec import EpisodeSpec, ImageStyle, SeriesSpec, VoiceConfig

from module1.prompt import build_phase_a_system

_OUTLINE_OPEN = "<<<OUTLINE>>>"
_OUTLINE_CLOSE = "<<<END_OUTLINE>>>"
_OUTLINE_LINE_RE = re.compile(r"^\s*(\d+)\s*\|\s*(.*?)\s*(?:\|\s*(.*?))?\s*$")

# UI history roles: "ai"/"user" (web.schemas.ChatMessage) → provider roles.
_ROLE_MAP = {"ai": "assistant", "user": "user"}


@dataclass
class OutlineItem:
    """A previewed episode (projection before approve, §4)."""

    id: str
    title: str
    desc: str
    pick: bool = True


@dataclass
class PhaseAResult:
    reply: str
    outline: list[OutlineItem] | None


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def build_messages(idea: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Assemble provider ``messages[]`` from UI history + the latest user idea (§3).

    ``history`` items are ``{role, text}`` with role in {"ai","user"} (UI shape).
    The latest ``idea`` is appended as the new user turn.
    """
    messages: list[dict[str, str]] = []
    for turn in history:
        role = _ROLE_MAP.get(turn.get("role", "user"), "user")
        content = str(turn.get("text", ""))
        if content:
            messages.append({"role": role, "content": content})
    idea = (idea or "").strip()
    if idea:
        messages.append({"role": "user", "content": idea})
    return messages


def parse_outline_preview(reply: str) -> list[OutlineItem] | None:
    """Parse the optional ``<<<OUTLINE>>>`` block into ``OutlineItem[]`` (§4).

    Returns ``None`` when the block is absent or yields no rows — non-fatal so the
    chat never breaks on a missing/garbled block.
    """
    start = reply.find(_OUTLINE_OPEN)
    if start == -1:
        return None
    start += len(_OUTLINE_OPEN)
    end = reply.find(_OUTLINE_CLOSE, start)
    block = reply[start:end] if end != -1 else reply[start:]

    items: list[OutlineItem] = []
    for line in block.splitlines():
        if not line.strip():
            continue
        m = _OUTLINE_LINE_RE.match(line)
        if not m:
            continue
        title = (m.group(2) or "").strip()
        desc = (m.group(3) or "").strip()
        if not title:
            continue
        items.append(OutlineItem(id=_new_id("w"), title=title, desc=desc, pick=True))
    return items or None


async def run_phase_a(
    idea: str,
    history: list[dict[str, str]],
    *,
    skill: str = "explain",
    language: str = "vi",
    provider: str = "stub-script",
    ctx: CallContext,
    registry: ServiceRegistry | None = None,
) -> PhaseAResult:
    """Run the refine chat turn (§4). Returns ``{reply, outline?}``.

    ``skill`` / ``language`` / ``provider`` default to safe values because Phase A
    may run before Setup (Open Q #1): the UI lets the user revise them at Setup.

    Phase A is TOPIC-AGNOSTIC (§4): the system prompt is a general video-planning
    assistant and does NOT depend on the skill — the skill is purely a *writing
    style* applied later at script generation, never a content gate here. The
    ``skill`` argument is accepted for forward-compatibility / call-site symmetry
    but is intentionally not used to shape the chat prompt.
    """
    reg = registry or get_registry()
    del skill  # not used in Phase A — kept topic-agnostic (§4)

    system = build_phase_a_system(language)
    messages = build_messages(idea, history)

    client = await reg.resolve(Task.WRITE_SCRIPT, provider, ctx)
    result = await client.write_script(ScriptRequest(messages=messages, system=system), ctx)
    reply = result.text or ""
    outline = parse_outline_preview(reply)
    return PhaseAResult(reply=reply, outline=outline)


# --------------------------------------------------------------------------- #
# Phase B — approve (build SeriesSpec shell, NO AI — D6)                       #
# --------------------------------------------------------------------------- #
def build_series_spec(
    *,
    name: str,
    topic: str,
    outline: list[dict[str, Any]],
    skill: str,
    language: str,
    target_minutes: float,
    density: str,
    providers: dict[str, str],
    voice: VoiceConfig,
    image_style: ImageStyle,
    series_id: str | None = None,
) -> SeriesSpec:
    """Assemble a :class:`SeriesSpec` shell from the edited outline + config (§6).

    Only ``pick == true`` outline rows become episodes (§6.1). Each episode is a
    shell: ``segments=[]``, ``youtube=None``, ``status='draft'`` (lazy gen later).
    No AI call (D6) — the edited outline is the source of truth.
    """
    sid = series_id or _new_id("s")
    picked = [o for o in outline if o.get("pick", True)]
    episodes: list[EpisodeSpec] = []
    for order, item in enumerate(picked, start=1):
        episodes.append(
            EpisodeSpec(
                episode_id=_new_id("e"),
                title=str(item.get("title", "")).strip() or f"Episode {order}",
                order=order,
                desc=(str(item.get("desc", "")).strip() or None),
                target_minutes=None,  # inherits series.target_minutes
                status="draft",
                youtube=None,
                segments=[],
            )
        )
    return SeriesSpec(
        series_id=sid,
        name=name.strip() or topic.strip() or "Untitled series",
        topic=topic.strip(),
        skill=skill,  # type: ignore[arg-type]  # validated at the request boundary
        language=language,
        target_minutes=target_minutes,
        density=density,  # type: ignore[arg-type]
        providers=dict(providers),
        image_style=image_style,
        voice=voice,
        episodes=episodes,
    )


__all__ = [
    "OutlineItem",
    "PhaseAResult",
    "build_messages",
    "parse_outline_preview",
    "run_phase_a",
    "build_series_spec",
]
