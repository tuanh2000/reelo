"""BYOK key endpoints (Module 3: reelo-ai-services).

``POST /keys`` validates (test call) then AES-256-GCM encrypts per-user.
``GET /keys/status`` returns presence/validity only — NEVER the key value.
``DELETE /keys/{key_ref}`` removes a key.

Validation policy (M3-5):
- :class:`InvalidKeyError` (401/403) -> reject with 400, do NOT store.
- :class:`ProviderUnavailableError` (service down / rate-limit) -> store with
  ``valid=None`` (couldn't verify now), surface ``valid=None`` to the UI.
- success -> store with ``valid=True``.
- keyless providers / providers with no registered client -> store ``valid=True``
  (nothing to validate).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from clients.base import InvalidKeyError, ProviderUnavailableError
from db.repository import ApiKeyRepo
from keystore import build_cipher_from_settings
from web._provider_keys import (
    build_validation_context,
    client_for_key_ref,
    resolve_key_ref,
)
from web.deps import CurrentUser, DbSession
from web.schemas import KeysStatusResponse, KeyStatus, SaveKeyRequest, SaveKeyResponse

router = APIRouter(prefix="/keys", tags=["keys"])


async def _validate(user_id: str, key_ref: str, value: str) -> bool | None:
    """Run the provider's cheap test call. Returns valid flag (None if unverifiable).

    Raises:
        HTTPException(400): on :class:`InvalidKeyError` (bad key) — caller does not store.
    """
    client = client_for_key_ref(key_ref)
    if client is None or not getattr(client, "requires_key", True):
        return True  # keyless or unknown -> nothing to validate
    ctx = build_validation_context(user_id, key_ref, value)
    try:
        return bool(await client.validate_key(ctx))
    except InvalidKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid key for {key_ref}: {exc}",
        ) from exc
    except ProviderUnavailableError:
        return None  # couldn't verify now; store unverified


@router.post("", response_model=SaveKeyResponse)
async def save_key(body: SaveKeyRequest, user_id: CurrentUser, db: DbSession) -> SaveKeyResponse:
    """Validate + encrypt + store a provider key per-user (maps ``saveApiKey``)."""
    key_ref = resolve_key_ref(body.provider)
    if not body.key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Key value is empty."
        )

    valid = await _validate(user_id, key_ref, body.key)

    cipher = build_cipher_from_settings()
    aad = f"{user_id}:{key_ref}".encode("utf-8")
    secret = cipher.encrypt(body.key, aad=aad)
    repo = ApiKeyRepo(db)
    await repo.upsert(
        user_id=user_id,
        key_ref=key_ref,
        ciphertext=secret.ciphertext,
        nonce=secret.nonce,
        valid=valid,
    )
    return SaveKeyResponse(key_ref=key_ref, valid=valid)


@router.get("/status", response_model=KeysStatusResponse)
async def keys_status(user_id: CurrentUser, db: DbSession) -> KeysStatusResponse:
    """Return ``{key_ref: {present, valid}}`` — no plaintext."""
    repo = ApiKeyRepo(db)
    rows = await repo.list_refs(user_id)
    keys = {row.key_ref: KeyStatus(present=True, valid=row.valid) for row in rows}
    return KeysStatusResponse(keys=keys)


@router.delete("/{key_ref}")
async def delete_key(key_ref: str, user_id: CurrentUser, db: DbSession) -> dict:
    """Delete the user's key for ``key_ref``."""
    repo = ApiKeyRepo(db)
    await repo.delete(user_id, key_ref)
    return {"deleted": key_ref}
