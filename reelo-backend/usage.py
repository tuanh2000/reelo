"""Per-user usage / cost recorder (M3-6).

:class:`UsageLogger` matches the :class:`clients.base.UsageLogger` Protocol.
Each successful AI call records ``(user_id, provider, task, units, cost)`` which
feeds the ``usage_log`` table and the cost-estimate UI (Module 2 §9).

Persistence is delegated to an injected :class:`UsageSink` so this module has no
hard DB dependency. The default sink buffers in memory (dev/tests + worker
batch flush); Module 3 wires a DB-backed sink. The pricing-math helper
:func:`compute_cost` lives here so all modules cost consistently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class UsageEvent:
    """One billable event. Mirrors a row of ``usage_log``."""

    user_id: str
    provider: str
    task: str
    units: float
    cost: float | None
    ts: datetime


@runtime_checkable
class UsageSink(Protocol):
    """Where recorded events go (DB in production)."""

    def write(self, event: UsageEvent) -> None: ...


@dataclass
class InMemoryUsageSink:
    """Default sink — keeps events in a list (dev/tests + worker buffer)."""

    events: list[UsageEvent] = field(default_factory=list)

    def write(self, event: UsageEvent) -> None:
        self.events.append(event)


class UsageLogger:
    """Records usage events. Satisfies :class:`clients.base.UsageLogger`."""

    def __init__(self, sink: UsageSink | None = None) -> None:
        self._sink: UsageSink = sink or InMemoryUsageSink()

    def record(
        self,
        user_id: str,
        provider: str,
        task: str,
        units: float,
        cost: float | None = None,
    ) -> None:
        self._sink.write(
            UsageEvent(
                user_id=user_id,
                provider=provider,
                task=task,
                units=units,
                cost=cost,
                ts=datetime.now(timezone.utc),
            )
        )


def compute_cost(task: str, units: float, pricing: dict[str, dict[str, float]]) -> float | None:
    """Compute cost from a provider's ``pricing`` block (services.yaml).

    Unit conventions (Module 3 §9):
    - write-script: ``units`` = total tokens; pricing has ``per_1k_input`` /
      ``per_1k_output``. Without an input/output split we bill at the output
      rate (callers pass a split via two calls when they have it).
    - generate-voice: ``units`` = characters; pricing has ``per_1k_chars``.
    - generate-image: ``units`` = image count; pricing has ``per_image``.

    Returns ``None`` if the task has no pricing entry (e.g. free providers with
    zeros still return ``0.0``; truly absent entries return ``None``).
    """
    block = pricing.get(task)
    if not block:
        return None
    if task == "write-script":
        rate = block.get("per_1k_output", block.get("per_1k_input", 0.0))
        return (units / 1000.0) * rate
    if task == "generate-voice":
        return (units / 1000.0) * block.get("per_1k_chars", 0.0)
    if task == "generate-image":
        return units * block.get("per_image", 0.0)
    return None


__all__ = [
    "UsageEvent",
    "UsageSink",
    "InMemoryUsageSink",
    "UsageLogger",
    "compute_cost",
]
