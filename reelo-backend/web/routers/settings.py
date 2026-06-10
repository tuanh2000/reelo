"""Account-level settings — PER-USER API key management ("Cấu hình AI").

Provider selection is PER-SERIES now (chosen in the create/Setup flow and stored
on the series). This page is reduced to managing the user's BYOK keys, which stay
PER-USER: entered once per provider, encrypted in the DB, and reused across every
series.

- ``GET /settings/providers`` → the per-task provider catalog + per-provider key
  status (``has_key`` / ``valid``) so the page can render "đã lưu / đã xác thực"
  badges and key inputs.

Key storage itself lives in ``POST /keys`` / ``GET /keys/status`` /
``DELETE /keys/{ref}`` (Module 3). Account-level "default provider" and
account-level voice-clone samples are gone (both are per-series now); the
``user_settings`` columns are left in place but unused (no migration needed).
"""

from __future__ import annotations

from fastapi import APIRouter

from clients.base import Task
from clients.registry import get_registry
from db.repository import ApiKeyRepo
from web._provider_keys import TASK_TO_FIELD, resolve_key_ref
from web.deps import CurrentUser, DbSession
from web.routers.providers import _PROVIDER_NOTES
from web.schemas import ProviderKeyItem, ProviderKeysResponse

router = APIRouter(prefix="/settings", tags=["settings"])


def _key_item(
    provider_id: str, raw: dict, field: str, key_state: dict[str, object]
) -> ProviderKeyItem:
    """Build a per-provider key item for the key-management page.

    ``key_state`` maps key_ref → validity (``True`` / ``False`` / ``None``) for the
    keys the user has saved. Keyless providers report ``requires_key=False`` and
    ``has_key=True`` (nothing to enter).
    """
    auth = raw.get("auth", {}) or {}
    requires_key = auth.get("type", "key") != "none"
    key_ref = resolve_key_ref(provider_id) if requires_key else None
    has_key = (key_ref in key_state) if (requires_key and key_ref) else (not requires_key)
    valid = key_state.get(key_ref) if (requires_key and key_ref) else None
    return ProviderKeyItem(
        id=provider_id,
        name=raw.get("display_name", provider_id),
        task=field,  # type: ignore[arg-type]
        cost_tier=raw.get("cost_tier", "paid"),
        requires_key=requires_key,
        has_key=has_key,
        valid=valid,  # type: ignore[arg-type]
        key_ref=key_ref,
        key_help_url=raw.get("key_help_url"),
        note=_PROVIDER_NOTES.get(provider_id),
    )


@router.get("/providers", response_model=ProviderKeysResponse)
async def get_provider_keys(
    user_id: CurrentUser, db: DbSession
) -> ProviderKeysResponse:
    """Return the per-task provider catalog + per-user key status (stubs hidden)."""
    rows = await ApiKeyRepo(db).list_refs(user_id)
    key_state: dict[str, object] = {row.key_ref: row.valid for row in rows}

    registry = get_registry()
    grouped: dict[str, list[ProviderKeyItem]] = {"script": [], "image": [], "voice": []}
    for provider_id, raw in registry.services_raw.items():
        if provider_id.startswith("stub-"):
            continue
        for task_value in (raw.get("tasks") or {}).keys():
            try:
                field = TASK_TO_FIELD[Task(task_value)]
            except (ValueError, KeyError):
                continue
            grouped[field].append(_key_item(provider_id, raw, field, key_state))

    return ProviderKeysResponse(
        script=grouped["script"], image=grouped["image"], voice=grouped["voice"]
    )


__all__ = ["router"]
