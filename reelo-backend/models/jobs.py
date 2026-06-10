"""Generation-job contracts shared between Module 2 (writer) and the UI (poller).

``GenJob`` matches ``reelo-ui/lib/data.ts`` 1:1 (``id, name, icon, state,
progress``) and is the body of ``GET /generation/{jobId}`` (a ``GenJob[]``).
``JobState`` mirrors the UI ``JobState`` union.

Changing these is a cross-module contract change (Module 2 ↔ UI) and must go
through the platform-lead.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

JobState = Literal["done", "running", "queued", "error"]


class GenJob(BaseModel):
    """A single child job in the produce pipeline. Mirrors UI ``GenJob``."""

    id: str
    name: str
    icon: str  # lucide icon id, e.g. "mic" | "image" | "film"
    state: JobState = "queued"
    progress: int = Field(default=0, ge=0, le=100)


# Canonical child-job kinds the produce pipeline seeds (Module 2 §8). The
# concrete N image jobs are created dynamically (image_1 .. image_N).
JobKind = Literal["voice", "image", "render", "thumbnail"]


__all__ = ["JobState", "GenJob", "JobKind"]
