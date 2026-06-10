"""BYOK key storage with AES-256-GCM encryption at rest (M3-4).

Two layers:

- :class:`Cipher` — the load-bearing crypto: AES-256-GCM encrypt/decrypt with a
  random 12-byte nonce per record, master key (KEK) from ``REELO_MASTER_KEY``.
  Fully implemented and unit-tested; this is shared infrastructure.
- :class:`KeyStore` — the user-facing store matching the :class:`clients.base.KeyStore`
  Protocol (``has`` / ``get`` / ``save`` / ``as_env``). It encrypts on write and
  decrypts on read. Persistence is delegated to an injected
  :class:`KeyRecordStore` so this module has no hard DB dependency; the worker
  preloads the user's records into the store before running a job, and Module 3
  wires the DB-backed record store + ``validate_key`` test call.

Security invariants:
- Plaintext keys are NEVER logged or returned by status endpoints.
- ``/keys/status`` returns presence/validity only (see web router).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12  # AES-GCM standard nonce length


@dataclass(frozen=True)
class EncryptedSecret:
    """An encrypted key as stored in ``api_keys`` (ciphertext includes GCM tag)."""

    ciphertext: bytes
    nonce: bytes


class Cipher:
    """AES-256-GCM envelope over a 32-byte master key (KEK)."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("master key must be exactly 32 bytes (AES-256)")
        self._aes = AESGCM(master_key)

    def encrypt(self, plaintext: str, *, aad: bytes | None = None) -> EncryptedSecret:
        nonce = os.urandom(NONCE_SIZE)
        ct = self._aes.encrypt(nonce, plaintext.encode("utf-8"), aad)
        return EncryptedSecret(ciphertext=ct, nonce=nonce)

    def decrypt(self, secret: EncryptedSecret, *, aad: bytes | None = None) -> str:
        pt = self._aes.decrypt(secret.nonce, secret.ciphertext, aad)
        return pt.decode("utf-8")


@runtime_checkable
class KeyRecordStore(Protocol):
    """Persistence backend for encrypted key records (DB-backed in production).

    Implemented by Module 3 over the ``api_keys`` repository. Sync signatures
    keep the :class:`clients.base.KeyStore` Protocol satisfiable; the worker
    preloads records so reads stay sync inside client calls.
    """

    def load(self, user_id: str, key_ref: str) -> EncryptedSecret | None: ...
    def store(self, user_id: str, key_ref: str, secret: EncryptedSecret) -> None: ...
    def exists(self, user_id: str, key_ref: str) -> bool: ...


@dataclass
class InMemoryKeyRecordStore:
    """Default record store — process-local dict of (user_id, key_ref) -> secret.

    Used in dev/tests and as the worker's preloaded cache. Module 3 swaps in a
    DB-backed implementation for the web process.
    """

    _records: dict[tuple[str, str], EncryptedSecret] = field(default_factory=dict)

    def load(self, user_id: str, key_ref: str) -> EncryptedSecret | None:
        return self._records.get((user_id, key_ref))

    def store(self, user_id: str, key_ref: str, secret: EncryptedSecret) -> None:
        self._records[(user_id, key_ref)] = secret

    def exists(self, user_id: str, key_ref: str) -> bool:
        return (user_id, key_ref) in self._records


class KeyStore:
    """Per-user BYOK key store. Satisfies :class:`clients.base.KeyStore`.

    Encrypts on :meth:`save`, decrypts on :meth:`get`. The ``aad`` binds each
    ciphertext to its ``user_id:key_ref`` so a record cannot be replayed under a
    different owner/ref.
    """

    def __init__(self, cipher: Cipher, records: KeyRecordStore | None = None) -> None:
        self._cipher = cipher
        self._records: KeyRecordStore = records or InMemoryKeyRecordStore()

    @staticmethod
    def _aad(user_id: str, key_ref: str) -> bytes:
        return f"{user_id}:{key_ref}".encode("utf-8")

    def has(self, user_id: str, key_ref: str) -> bool:
        return self._records.exists(user_id, key_ref)

    def get(self, user_id: str, key_ref: str) -> str | None:
        secret = self._records.load(user_id, key_ref)
        if secret is None:
            return None
        return self._cipher.decrypt(secret, aad=self._aad(user_id, key_ref))

    def save(self, user_id: str, key_ref: str, value: str) -> None:
        secret = self._cipher.encrypt(value, aad=self._aad(user_id, key_ref))
        self._records.store(user_id, key_ref, secret)

    def as_env(self, user_id: str, mapping: dict[str, str]) -> dict[str, str]:
        """Build an env dict for a skill subprocess.

        ``mapping`` is ``{ENV_VAR: key_ref}``. Only present keys are included;
        values are decrypted just-in-time and never logged.
        """
        env: dict[str, str] = {}
        for env_var, key_ref in mapping.items():
            value = self.get(user_id, key_ref)
            if value is not None:
                env[env_var] = value
        return env


def build_cipher_from_settings() -> Cipher:
    """Construct a :class:`Cipher` from ``REELO_MASTER_KEY`` (raises if unset)."""
    from config import get_settings

    return Cipher(get_settings().master_key_bytes())


__all__ = [
    "NONCE_SIZE",
    "EncryptedSecret",
    "Cipher",
    "KeyRecordStore",
    "InMemoryKeyRecordStore",
    "KeyStore",
    "build_cipher_from_settings",
]
