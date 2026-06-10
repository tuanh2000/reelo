"""DB-backed BYOK key + usage backends (Module 3).

Bridges the sync :class:`keystore.KeyRecordStore` / :class:`usage.UsageSink`
Protocols (which clients call synchronously inside a request) to the async
``ApiKeyRepo`` / ``UsageRepo``.

Two shapes, because the Protocols are sync but the DB is async:

- :class:`PreloadedKeyRecordStore` — an in-memory snapshot of a user's encrypted
  key records, loaded once (async) via :func:`load_user_keystore` before client
  calls run. This is what the worker uses (Module 3 §8): preload → run job →
  reads stay sync. It also collects writes so they can be flushed back to the DB.
- :class:`BufferingUsageSink` — buffers :class:`usage.UsageEvent` in memory; the
  worker flushes the buffer to ``usage_log`` after the job via
  :func:`flush_usage`.

The web process does not use these for validate-on-save: the ``/keys`` router
encrypts with :class:`keystore.Cipher` and writes through ``ApiKeyRepo``
directly (async), and validates via a one-off :class:`keystore.KeyStore` wrapped
around a one-record store.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from db.repository import ApiKeyRepo, UsageRepo
from keystore import EncryptedSecret, KeyStore
from usage import UsageEvent, UsageLogger


@dataclass
class PreloadedKeyRecordStore:
    """Sync key-record store seeded from the DB; collects new writes to flush.

    Satisfies :class:`keystore.KeyRecordStore`. Reads/exists hit the in-memory
    snapshot; ``store`` updates the snapshot and records the (user_id, key_ref)
    as dirty so the caller can persist it afterwards.
    """

    _records: dict[tuple[str, str], EncryptedSecret] = field(default_factory=dict)
    _dirty: set[tuple[str, str]] = field(default_factory=set)

    def load(self, user_id: str, key_ref: str) -> EncryptedSecret | None:
        return self._records.get((user_id, key_ref))

    def store(self, user_id: str, key_ref: str, secret: EncryptedSecret) -> None:
        self._records[(user_id, key_ref)] = secret
        self._dirty.add((user_id, key_ref))

    def exists(self, user_id: str, key_ref: str) -> bool:
        return (user_id, key_ref) in self._records

    def seed(self, user_id: str, key_ref: str, secret: EncryptedSecret) -> None:
        """Insert a record without marking it dirty (used by preload)."""
        self._records[(user_id, key_ref)] = secret

    @property
    def dirty(self) -> set[tuple[str, str]]:
        return self._dirty


async def load_user_keystore(repo: ApiKeyRepo, cipher, user_id: str) -> KeyStore:
    """Build a :class:`KeyStore` preloaded with all of ``user_id``'s key records.

    Run once (async) before client calls; subsequent ``has``/``get`` are sync.
    """
    store = PreloadedKeyRecordStore()
    for row in await repo.list_refs(user_id):
        store.seed(
            user_id, row.key_ref, EncryptedSecret(ciphertext=row.ciphertext, nonce=row.nonce)
        )
    return KeyStore(cipher, records=store)


@dataclass
class BufferingUsageSink:
    """Sync usage sink that buffers events for an async flush.

    Satisfies :class:`usage.UsageSink`.
    """

    events: list[UsageEvent] = field(default_factory=list)

    def write(self, event: UsageEvent) -> None:
        self.events.append(event)


async def flush_usage(repo: UsageRepo, logger: UsageLogger) -> int:
    """Persist a buffering logger's events to ``usage_log``. Returns count flushed."""
    sink = getattr(logger, "_sink", None)
    if not isinstance(sink, BufferingUsageSink):
        return 0
    count = 0
    for ev in sink.events:
        await repo.add(
            user_id=ev.user_id,
            provider=ev.provider,
            task=ev.task,
            units=ev.units,
            cost=ev.cost,
        )
        count += 1
    sink.events.clear()
    return count


def new_buffering_usage_logger() -> UsageLogger:
    return UsageLogger(sink=BufferingUsageSink())


__all__ = [
    "PreloadedKeyRecordStore",
    "load_user_keystore",
    "BufferingUsageSink",
    "flush_usage",
    "new_buffering_usage_logger",
]
