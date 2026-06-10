"""Thin async repository layer over the ORM models.

Repositories carry no business logic — they are typed CRUD scoped by
``user_id`` so callers cannot accidentally cross tenants. Module owners extend
these with the queries they need; the platform-lead keeps the constructor
shape (``Repo(session)``) and the user-scoping invariant.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ApiKey, Episode, GenJobRow, Series, UsageLog, User, UserSettings

# Default account-level providers when a user has never configured anything.
# script/image stay unset (None) so the UI gate forces the user to choose one;
# voice defaults to the free keyless Edge-TTS so a brand-new account can at least
# produce voice without configuration.
DEFAULT_PROVIDERS: dict[str, str | None] = {"script": None, "image": None, "voice": "edge"}


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


class UserSettingsRepo:
    """Account-level settings (provider choices), one row per user.

    ``get_providers`` always returns a complete ``{script, image, voice}`` dict,
    merging the user's saved choices over :data:`DEFAULT_PROVIDERS` so callers
    never have to special-case a missing row. ``set_providers`` upserts only the
    keys supplied (partial update), leaving the others intact.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    @staticmethod
    def default_providers() -> dict[str, str | None]:
        return dict(DEFAULT_PROVIDERS)

    async def get(self, user_id: str) -> UserSettings | None:
        return await self.s.get(UserSettings, user_id)

    async def get_providers(self, user_id: str) -> dict[str, str | None]:
        """Return the full provider triple (saved choices over defaults)."""
        row = await self.get(user_id)
        merged = dict(DEFAULT_PROVIDERS)
        if row is not None:
            saved = (row.settings or {}).get("providers") or {}
            for task in ("script", "image", "voice"):
                if task in saved:
                    merged[task] = saved[task]
        return merged

    async def set_providers(
        self, user_id: str, providers: dict[str, str | None]
    ) -> dict[str, str | None]:
        """Upsert the supplied provider keys; returns the merged triple."""
        row = await self.get(user_id)
        if row is None:
            row = UserSettings(user_id=user_id, settings={"providers": {}})
            self.s.add(row)
        settings = dict(row.settings or {})
        current = dict(settings.get("providers") or {})
        for task, value in providers.items():
            current[task] = value
        settings["providers"] = current
        row.settings = settings
        await self.s.flush()
        merged = dict(DEFAULT_PROVIDERS)
        merged.update({k: v for k, v in current.items() if k in merged})
        return merged

    async def get_voice_sample(self, user_id: str) -> dict[str, Any] | None:
        """Return the account-level voice-clone sample blob, or ``None``.

        Shape (when present): ``{"audio_key", "transcript", "language"}`` — the
        object-storage key of the normalized (wav 24 kHz mono) reference clip,
        the exact text spoken in it, and its language code. Used by OmniVoice
        clone mode and snapshotted into a series at approve time.
        """
        row = await self.get(user_id)
        if row is None:
            return None
        sample = (row.settings or {}).get("voice_sample")
        return dict(sample) if isinstance(sample, dict) else None

    async def set_voice_sample(
        self, user_id: str, sample: dict[str, Any]
    ) -> dict[str, Any]:
        """Upsert the account-level voice-clone sample; returns the stored blob.

        ``sample`` is ``{"audio_key", "transcript", "language"}``. Stored in the
        JSONB ``settings`` blob alongside ``providers`` (no migration needed).
        """
        row = await self.get(user_id)
        if row is None:
            row = UserSettings(user_id=user_id, settings={"providers": {}})
            self.s.add(row)
        settings = dict(row.settings or {})
        settings["voice_sample"] = dict(sample)
        row.settings = settings
        await self.s.flush()
        return dict(sample)


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

    async def rename(self, user_id: str, series_id: str, name: str) -> Series | None:
        """Rename a series, keeping the ``name`` column and ``spec_json.name`` in sync.

        Scoped by ``user_id`` (returns ``None`` when the series is missing / not
        owned). Updates both the denormalized ``Series.name`` column AND the
        ``name`` inside the JSONB ``spec_json`` (the source of truth) so the two
        never drift. The caller is responsible for validating/trimming ``name``.
        """
        row = await self.get(user_id, series_id)
        if row is None:
            return None
        row.name = name
        spec = dict(row.spec_json or {})
        spec["name"] = name
        row.spec_json = spec
        await self.s.flush()
        return row

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

    async def set_script_state(
        self,
        user_id: str,
        episode_id: str,
        script_status: str,
        error: str | None = None,
    ) -> Episode | None:
        """Record lazy-script-gen progress so the UI can surface it (status + error).

        ``script_status`` is ``"running" | "done" | "error"``. ``error`` is a short
        human-readable message (provider + cause), set only when status is
        ``"error"`` and cleared on every ``running``/``done`` transition. Kept in
        the existing ``paths`` JSONB (no migration) under the ``script_status`` /
        ``script_error`` keys, merged so asset keys are not clobbered.

        On the ``running`` transition we also stamp ``script_started_at`` (ISO-8601
        UTC) so the UI can compute "đã viết X giây" from a SERVER mốc thời gian
        instead of a client timer (which resets on tab-switch / remount). It is
        only (re)written when there is no live ``script_started_at`` yet, so a
        re-fetch that re-marks ``running`` does not keep resetting the clock; it is
        cleared on ``done``/``error`` so a later run starts fresh.
        """
        ep = await self.get(user_id, episode_id)
        if ep is None:
            return None
        paths = {**(ep.paths or {})}
        paths["script_status"] = script_status
        if script_status == "running":
            if not paths.get("script_started_at"):
                paths["script_started_at"] = (
                    datetime.now(timezone.utc).isoformat()
                )
            paths.pop("script_error", None)
        elif script_status == "error":
            paths["script_error"] = error or "unknown error"
            paths.pop("script_started_at", None)
        else:  # done
            paths.pop("script_error", None)
            paths.pop("script_started_at", None)
        ep.paths = paths
        await self.s.flush()
        return ep

    @staticmethod
    def script_state(paths: dict[str, Any] | None) -> tuple[str | None, str | None]:
        """Project ``(script_status, script_error)`` out of an episode's ``paths``."""
        p = paths or {}
        status = p.get("script_status")
        return (status if isinstance(status, str) else None), p.get("script_error")

    @staticmethod
    def script_started_at(paths: dict[str, Any] | None) -> str | None:
        """Project the ISO ``script_started_at`` server timestamp out of ``paths``."""
        started = (paths or {}).get("script_started_at")
        return started if isinstance(started, str) else None

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

    async def reset_to_draft(self, user_id: str, episode_id: str) -> Episode | None:
        """Reset an episode row back to fresh draft (destructive — see reset endpoint).

        Clears the produce/script artefacts tracked on the row so a later produce
        run starts clean: status→``draft``, ``paths`` (asset keys + the resume
        ``asset_manifest`` + ``script_status``/``script_error``/``script_started_at``)
        and ``urls`` emptied, and ``image_curation`` dropped. The caller is
        responsible for (a) clearing ``segments`` in ``spec_json`` and (b) deleting
        the gen_jobs + storage assets. Scoped by ``user_id``; returns the row or
        ``None`` when missing.
        """
        ep = await self.get(user_id, episode_id)
        if ep is None:
            return None
        ep.status = "draft"
        ep.paths = {}
        ep.urls = {}
        ep.image_curation = None
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

    async def latest_parent_for_episode(
        self, user_id: str, episode_id: str
    ) -> GenJobRow | None:
        """Most-recent parent (produce) job for an episode, or ``None``.

        Used to reconstruct the workspace's "đang sản xuất" view from the backend
        after a tab-switch / navigate-away / refresh: the client no longer needs to
        hold the ``jobId`` — it is recovered from the latest parent row. Ordered by
        ``created_at`` (then ``id`` as a tiebreak) so the newest run wins.
        """
        res = await self.s.execute(
            select(GenJobRow)
            .where(
                GenJobRow.episode_id == episode_id,
                GenJobRow.user_id == user_id,
                GenJobRow.parent_id.is_(None),
            )
            .order_by(GenJobRow.created_at.desc(), GenJobRow.id.desc())
        )
        return res.scalars().first()

    async def add(self, row: GenJobRow) -> GenJobRow:
        self.s.add(row)
        await self.s.flush()
        return row

    async def delete_for_episode(self, user_id: str, episode_id: str) -> int:
        """Delete every gen_jobs row (parent + children) for an episode. Returns count.

        Used by the destructive episode reset so a fresh produce seeds a clean job
        tree. Children FK-cascade on ``parent_id``, but we delete by episode so a
        parentless/orphan row is removed too. Scoped by ``user_id``.
        """
        res = await self.s.execute(
            select(GenJobRow).where(
                GenJobRow.episode_id == episode_id, GenJobRow.user_id == user_id
            )
        )
        rows = list(res.scalars().all())
        for row in rows:
            await self.s.delete(row)
        await self.s.flush()
        return len(rows)


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
    "UserSettingsRepo",
    "DEFAULT_PROVIDERS",
    "SeriesRepo",
    "EpisodeRepo",
    "GenJobRepo",
    "ApiKeyRepo",
    "UsageRepo",
]
