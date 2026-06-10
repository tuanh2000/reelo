"""Thin async repository layer over the ORM models.

Repositories carry no business logic — they are typed CRUD scoped by
``user_id`` so callers cannot accidentally cross tenants. Module owners extend
these with the queries they need; the platform-lead keeps the constructor
shape (``Repo(session)``) and the user-scoping invariant.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ApiKey, Episode, GenJobRow, Series, UsageLog, User


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, user_id: str) -> User | None:
        return await self.s.get(User, user_id)

    async def get_by_google_sub(self, google_sub: str) -> User | None:
        res = await self.s.execute(select(User).where(User.google_sub == google_sub))
        return res.scalar_one_or_none()

    async def upsert_from_oauth(
        self, *, user_id: str, google_sub: str, email: str, name: str | None, picture: str | None
    ) -> User:
        user = await self.get_by_google_sub(google_sub)
        if user is None:
            user = User(
                id=user_id, google_sub=google_sub, email=email, name=name, picture=picture
            )
            self.s.add(user)
        else:
            user.email = email
            user.name = name
            user.picture = picture
        await self.s.flush()
        return user


class SeriesRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def list_for_user(self, user_id: str) -> list[Series]:
        res = await self.s.execute(
            select(Series).where(Series.user_id == user_id).order_by(Series.created_at.desc())
        )
        return list(res.scalars().all())

    async def get(self, user_id: str, series_id: str) -> Series | None:
        res = await self.s.execute(
            select(Series).where(Series.id == series_id, Series.user_id == user_id)
        )
        return res.scalar_one_or_none()

    async def upsert(
        self,
        *,
        user_id: str,
        series_id: str,
        name: str,
        topic: str,
        skill: str,
        spec_json: dict[str, Any],
    ) -> Series:
        row = await self.get(user_id, series_id)
        if row is None:
            row = Series(
                id=series_id,
                user_id=user_id,
                name=name,
                topic=topic,
                skill=skill,
                spec_json=spec_json,
            )
            self.s.add(row)
        else:
            row.name = name
            row.topic = topic
            row.skill = skill
            row.spec_json = spec_json
        await self.s.flush()
        return row


class EpisodeRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, user_id: str, episode_id: str) -> Episode | None:
        res = await self.s.execute(
            select(Episode).where(Episode.id == episode_id, Episode.user_id == user_id)
        )
        return res.scalar_one_or_none()

    async def list_for_series(self, user_id: str, series_id: str) -> list[Episode]:
        res = await self.s.execute(
            select(Episode)
            .where(Episode.series_id == series_id, Episode.user_id == user_id)
            .order_by(Episode.order)
        )
        return list(res.scalars().all())

    async def set_status(self, user_id: str, episode_id: str, status: str) -> None:
        ep = await self.get(user_id, episode_id)
        if ep is not None:
            ep.status = status
            await self.s.flush()

    async def get_curation(self, user_id: str, episode_id: str) -> dict[str, Any] | None:
        """Return the episode's ``image_curation`` blob (M2-12), or None."""
        ep = await self.get(user_id, episode_id)
        return ep.image_curation if ep is not None else None

    async def set_curation(
        self, user_id: str, episode_id: str, curation: dict[str, Any]
    ) -> Episode | None:
        """Persist the episode's ``image_curation`` blob (candidates + choices).

        Replaces the whole blob (the caller computes the merged state). Returns the
        updated row, or ``None`` if the episode is missing.
        """
        ep = await self.get(user_id, episode_id)
        if ep is None:
            return None
        ep.image_curation = curation
        await self.s.flush()
        return ep

    async def set_paths(
        self,
        user_id: str,
        episode_id: str,
        paths: dict[str, Any],
        *,
        urls: dict[str, Any] | None = None,
        status: str | None = None,
        merge: bool = True,
    ) -> Episode | None:
        """Persist asset locations (and optionally signed URLs / status).

        Module 2's runner calls this after upload instead of mutating the ORM row
        directly. ``merge=True`` (default) shallow-merges into the existing
        ``paths``/``urls`` dicts so partial updates don't clobber prior keys.
        Returns the updated row (or ``None`` if the episode is missing).
        """
        ep = await self.get(user_id, episode_id)
        if ep is None:
            return None
        ep.paths = {**(ep.paths or {}), **paths} if merge else dict(paths)
        if urls is not None:
            ep.urls = {**(ep.urls or {}), **urls} if merge else dict(urls)
        if status is not None:
            ep.status = status
        await self.s.flush()
        return ep


class GenJobRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def children_for_episode(self, user_id: str, episode_id: str) -> list[GenJobRow]:
        res = await self.s.execute(
            select(GenJobRow)
            .where(
                GenJobRow.episode_id == episode_id,
                GenJobRow.user_id == user_id,
                GenJobRow.parent_id.is_not(None),
            )
            .order_by(GenJobRow.created_at)
        )
        return list(res.scalars().all())

    async def get(self, user_id: str, job_id: str) -> GenJobRow | None:
        res = await self.s.execute(
            select(GenJobRow).where(GenJobRow.id == job_id, GenJobRow.user_id == user_id)
        )
        return res.scalar_one_or_none()

    async def add(self, row: GenJobRow) -> GenJobRow:
        self.s.add(row)
        await self.s.flush()
        return row


class ApiKeyRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def get(self, user_id: str, key_ref: str) -> ApiKey | None:
        res = await self.s.execute(
            select(ApiKey).where(ApiKey.user_id == user_id, ApiKey.key_ref == key_ref)
        )
        return res.scalar_one_or_none()

    async def list_refs(self, user_id: str) -> list[ApiKey]:
        res = await self.s.execute(select(ApiKey).where(ApiKey.user_id == user_id))
        return list(res.scalars().all())

    async def upsert(
        self,
        *,
        user_id: str,
        key_ref: str,
        ciphertext: bytes,
        nonce: bytes,
        valid: bool | None,
    ) -> ApiKey:
        row = await self.get(user_id, key_ref)
        if row is None:
            row = ApiKey(
                user_id=user_id, key_ref=key_ref, ciphertext=ciphertext, nonce=nonce, valid=valid
            )
            self.s.add(row)
        else:
            row.ciphertext = ciphertext
            row.nonce = nonce
            row.valid = valid
        await self.s.flush()
        return row

    async def delete(self, user_id: str, key_ref: str) -> None:
        await self.s.execute(
            delete(ApiKey).where(ApiKey.user_id == user_id, ApiKey.key_ref == key_ref)
        )


class UsageRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def add(
        self,
        *,
        user_id: str,
        provider: str,
        task: str,
        units: float,
        cost: float | None,
    ) -> UsageLog:
        row = UsageLog(
            user_id=user_id, provider=provider, task=task, units=units, cost=cost
        )
        self.s.add(row)
        await self.s.flush()
        return row

    async def list_for_user(self, user_id: str) -> list[UsageLog]:
        res = await self.s.execute(
            select(UsageLog).where(UsageLog.user_id == user_id).order_by(UsageLog.ts.desc())
        )
        return list(res.scalars().all())


__all__ = [
    "UserRepo",
    "SeriesRepo",
    "EpisodeRepo",
    "GenJobRepo",
    "ApiKeyRepo",
    "UsageRepo",
]
