"""SQLAlchemy 2.x ORM models — shared persistence (Postgres).

Every resource is scoped by ``user_id`` (multi-tenant, integration §5). The
``Series.spec_json`` column holds a serialized :class:`models.spec.SeriesSpec`
(JSONB). These models are a cross-module contract; changes go through the
platform-lead.

Tables: ``users``, ``series``, ``episodes``, ``gen_jobs``, ``api_keys``,
``usage_log``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base. ``Base.metadata`` is the Alembic target."""


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # internal user id
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    picture: Mapped[str | None] = mapped_column(Text)

    series: Mapped[list["Series"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserSettings(Base, TimestampMixin):
    """Account-level configuration, one row per user (1:1 with ``users``).

    Holds the user's chosen AI providers for the three generation tasks
    (script / image / voice). These are configured ONCE in the Settings screen
    and shared across every series the user creates (decision: account-level, a
    single provider set per task). ``settings`` is a JSONB blob so new
    account-level preferences can be added without further migrations; the
    canonical shape today is::

        {"providers": {"script": <id|null>, "image": <id|null>, "voice": <id>}}

    Defaults when a user has never configured anything: ``script=None``,
    ``image=None``, ``voice="edge"`` (free, keyless) — see
    :func:`db.repository.UserSettingsRepo.default_providers`.
    """

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class Series(Base, TimestampMixin):
    __tablename__ = "series"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    topic: Mapped[str] = mapped_column(Text, default="")
    skill: Mapped[str] = mapped_column(String(32), nullable=False)
    # Full SeriesSpec (models.spec.SeriesSpec) serialized as JSONB.
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    user: Mapped[User] = relationship(back_populates="series")
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )


class Episode(Base, TimestampMixin):
    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    series_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("series.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    # draft -> scripted -> assets -> assembled -> published
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    # Asset locations on object storage (keys) and/or signed URLs filled post-render.
    paths: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # {script, final, srt, ...}
    urls: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # signed URLs (transient)
    # Human image-curation state for web-photo providers (M2-12). Pre-produce data
    # kept OUT of the canonical SeriesSpec: per-segment candidate lists + the
    # user's chosen photo. Shape:
    #   {"provider": "web-commons",
    #    "segments": [{"index", "query", "candidates": [...], "chosen_id"}]}
    # None/empty for AI providers and un-curated episodes (runner falls back to auto).
    image_curation: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    series: Mapped[Series] = relationship(back_populates="episodes")
    jobs: Mapped[list["GenJobRow"]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )


class GenJobRow(Base, TimestampMixin):
    """A generation job row — parent or child (Module 2 §8). Polled by the UI.

    The UI-facing :class:`models.jobs.GenJob` projects from child rows.
    """

    __tablename__ = "gen_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    episode_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("episodes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("gen_jobs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # voice|image|render|thumbnail|parent
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # UI display name
    icon: Mapped[str] = mapped_column(String(64), default="")  # lucide icon id
    state: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    stderr: Mapped[str | None] = mapped_column(Text)  # captured on error

    episode: Mapped[Episode] = relationship(back_populates="jobs")


class ApiKey(Base, TimestampMixin):
    """Encrypted BYOK key (AES-256-GCM). Never stores plaintext (M3-4)."""

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "key_ref", name="uq_api_keys_user_keyref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    key_ref: Mapped[str] = mapped_column(String(64), nullable=False)  # logical id e.g. "elevenlabs"
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # includes GCM tag
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    valid: Mapped[bool | None] = mapped_column()  # last validate_key result (M3-5)


class UsageLog(Base):
    """Per-user usage / cost record (M3-6)."""

    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(32), nullable=False)
    units: Mapped[float] = mapped_column(Float, default=0.0)
    cost: Mapped[float | None] = mapped_column(Float)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


__all__ = [
    "Base",
    "TimestampMixin",
    "User",
    "UserSettings",
    "Series",
    "Episode",
    "GenJobRow",
    "ApiKey",
    "UsageLog",
]
