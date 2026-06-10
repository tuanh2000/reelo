"""Shared Pydantic contracts (cross-module). See :mod:`models.spec` and
:mod:`models.jobs`. These shapes are owned by the platform-lead."""

from models.jobs import GenJob, JobKind, JobState
from models.spec import (
    Aspect,
    Density,
    EpisodeSpec,
    EpisodeStatus,
    ImageStyle,
    SegmentSpec,
    SeriesSpec,
    Skill,
    VoiceConfig,
)

__all__ = [
    "Aspect",
    "Density",
    "Skill",
    "EpisodeStatus",
    "SegmentSpec",
    "EpisodeSpec",
    "ImageStyle",
    "VoiceConfig",
    "SeriesSpec",
    "GenJob",
    "JobKind",
    "JobState",
]
